import os
import asyncio
import contextlib
import hashlib
import json
import signal
import tempfile
import shutil
import sys
import re
import shlex
import subprocess
from typing import Optional, List, Dict, Any

from pydantic import Field, ConfigDict, PrivateAttr

from agentevolver.benchmark.types import Benchmark, Task, Stats, EvaluationResult
from agentevolver.logger import logger
from agentevolver.registry import BENCHMARK
from agentevolver.paths import P, path_manager
from agentevolver.utils import dedent

SYSTEM_PROMPT = dedent("""
    You are an expert software engineer. You are given only a compiled binary of a
    program together with its documentation, running inside an offline sandbox.
    Your task is to architect and implement, from scratch, a complete codebase that
    reproduces the original program's observable behavior as faithfully as possible.

    Work entirely from the binary and the documentation — you have no internet access
    and no access to the original source code. Your final deliverable is the complete
    source tree of your reconstructed program.

    Task:
""")


@BENCHMARK.register_module(force=True)
class ProgramBenchmark(Benchmark):
    """
    ProgramBench – agents must rebuild a complete codebase that reproduces a program's
    behavior given only its compiled binary and documentation. Scored by running the
    hidden test suites against the agent's reconstructed codebase (passed / total).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="programbench", description="The name of the benchmark")
    # Task definitions (task.yaml/tests.json) ship with the `programbench` pip package.
    # `path`/`hf_repo_id` govern the per-branch TEST BLOBS, read locally first and
    # downloaded from HuggingFace on demand by the official evaluator.
    path: str = Field(
        default="datasets/ProgramBench-Tests",
        description="Local directory holding the per-branch test blobs.",
    )
    hf_repo_id: str = Field(
        default="programbench/ProgramBench-Tests",
        description="HuggingFace repo to download the test blobs from when missing locally.",
    )

    # Evaluation knobs (passed through to the `programbench eval` CLI)
    docker_cpus: int = Field(
        default=10, description="CPU cores allotted per docker container during evaluation."
    )
    branch_workers: int = Field(
        default=1, description="Number of test branches to run in parallel within an instance."
    )
    eval_timeout: int = Field(
        default=3600,
        description="Per-task timeout (seconds) for the `programbench eval` subprocess.",
    )

    _data_records: List[Dict] = PrivateAttr(default_factory=list)
    _instances: Dict[str, Dict] = PrivateAttr(default_factory=dict)

    system_prompt: Optional[str] = Field(
        default=SYSTEM_PROMPT, description="The system prompt for the benchmark"
    )

    container_workspace: str = "/workspace"
    submission_protocol: str = "programbench-final-only-v1"
    submission_filename: str = "frozen/submission.json"
    reference_copy: str = "reference_executable"
    scaffolding: tuple[str, ...] = ("reference_executable", "plan.md")

    def _package_submission(self, workspace: str, dest_dir: str) -> dict:
        """Package the reconstruction from `workspace` into `dest_dir/submission.tar.gz`.

        Prefers the committed tree (``git archive HEAD``), which is what makes the task
        document's "commit your solution" and ".gitignore your build artifacts" mean
        anything: an agent that ignores its scratch files then ships a clean submission.
        Left to a plain tar of the directory, neither instruction affected the deliverable
        at all — one run shipped 70 files of which 6 were the solution, the rest comparison
        dumps like `ref_dumb.out` and `my_V.od`.

        With one exception, and it is the important one: **if the archive does not contain
        `compile.sh`, ship the whole tree instead.** A submission without it scores zero by
        definition, so when the committed tree lacks the build script — the agent forgot to
        commit, or committed only part of its work — the fuller tree can only help. Being
        strict there would turn a scoring mistake into a guaranteed zero.

        Either way the stashed reference binary is excluded. The official grader also
        deletes byte-identical copies of it by hash, so this is the belt to that braces.

        Returns a description of what was shipped, for the run's results file.
        """
        import tarfile

        os.makedirs(dest_dir, exist_ok=True)
        tarball = str(path_manager.under(dest_dir, P.PROJECT_SUBMISSION))

        def _git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", workspace, *args], capture_output=True, text=True, timeout=120
            )

        info: dict = {"source": None, "uncommitted": [], "note": None}
        counted = _git("rev-list", "--count", "HEAD")
        commit_count = int(counted.stdout.strip() or 0) if counted.returncode == 0 else 0
        status = _git("status", "--porcelain", "--untracked-files=all")
        if status.returncode == 0:
            info["uncommitted"] = [
                entry
                for entry in (line[3:] for line in status.stdout.splitlines())
                if entry and (not entry.split("/")[0] in self.scaffolding)
            ]
        if commit_count > 1:
            archived = _git("archive", "--format=tar.gz", "-o", tarball, "HEAD")
            if archived.returncode != 0:
                info["note"] = (
                    f"git archive failed ({archived.stderr.strip()[:200]}); shipped the whole workspace"
                )
            else:
                with tarfile.open(tarball, "r:gz") as handle:
                    names = handle.getnames()
                if any((name.lstrip("./") == "compile.sh" for name in names)):
                    info["source"] = "git-archive"
                else:
                    info["note"] = (
                        "the committed tree has no compile.sh, so the whole workspace was shipped instead — a submission without it scores zero"
                    )
        else:
            info["note"] = (
                "no commit beyond the repository's initial one; shipped the whole workspace"
                if commit_count
                else "no commits at all; shipped the whole workspace"
            )
        if info["source"] is None:
            info["source"] = "full-tree"
            unreadable = []
            with tarfile.open(tarball, "w:gz") as handle:
                for entry in sorted(os.listdir(workspace)):
                    if entry in self.scaffolding:
                        continue
                    try:
                        handle.add(os.path.join(workspace, entry), arcname=entry)
                    except (PermissionError, OSError) as error:
                        unreadable.append(f"{entry} ({error.strerror or error})")
            if unreadable:
                info["skipped_unreadable"] = unreadable
                logger.warning(
                    f"| ⚠️ Left out of the submission, unreadable: {', '.join(unreadable)}"
                )
        extract_dir = str(path_manager.under(dest_dir, P.PROJECT_SUBMISSION_VIEW))
        with tarfile.open(tarball, "r:gz") as handle:
            handle.extractall(extract_dir, filter="data")
        info["tarball"] = tarball
        info["files"] = len(os.listdir(extract_dir))
        return info

    def _reads_the_binary_as_a_file(self, command: str, tools) -> bool:
        """True when an analysis tool is pointed at the reference binary itself.

        The distinction that matters: `strings reference_executable` reads the binary, while
        `./reference_executable -V | hexdump -C` reads what the binary *printed*. The second
        is allowed and useful — exact leading spaces and line endings are part of the
        behaviour being reproduced — so the audit has to look at what the tool was given, not
        at which words appear in the command.

        Conservative in the direction of flagging: a command whose shape this cannot parse and
        which names both a tool and the binary is reported, because a missed violation is
        worse than a false one that a human checks.
        """
        import shlex

        for fragment in re.split("[|;&\\n]+", command):
            try:
                argv = shlex.split(fragment)
            except ValueError:
                argv = fragment.split()
            if not argv:
                continue
            name = os.path.basename(argv[0]).lower()
            if not any((name == tool.strip() for tool in tools)):
                continue
            operands = [a for a in argv[1:] if not a.startswith("-")]
            for operand in operands:
                base = os.path.basename(operand)
                if base in (self.reference_copy, "executable"):
                    return True
        return False

    def _audit_reference_binary(self, log_root: str) -> dict:
        """Check the run's own trace for binary analysis of the reference.

        The task rules forbid inspecting the reference binary as a *file*; the official
        image enforces that with permissions, by running as a user who cannot read it. This
        run is root, which bypasses the permission bits — so the prohibition holds by
        instruction rather than by the filesystem, and an instruction is worth checking.
        Recording the answer makes "no binary analysis was used" substantiable rather than
        assumed.

        Analysis of the agent's *own* build is explicitly allowed, so only actions naming
        the reference are flagged.
        """
        tools = (
            "objdump",
            "readelf",
            "strings",
            "nm ",
            "xxd",
            "hexdump",
            "gdb",
            "ltrace",
            "strace",
        )
        hits: list = []
        trace_dir = path_manager.under(log_root, P.LOG_MODULE, module="trace")
        for path in sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []:
            rows = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
            for index, event in enumerate(rows):
                payload = event.get("input")
                if not isinstance(payload, dict):
                    continue
                command = " ".join(
                    (str(payload.get(key, "")) for key in ("command", "code", "args", "path"))
                )
                if not command.strip():
                    continue
                if self.reference_copy not in command and "./executable" not in command:
                    continue
                if not self._reads_the_binary_as_a_file(command, tools):
                    continue
                outcome = str(event.get("output") or "")
                if not outcome:
                    for neighbour in rows[index : index + 3]:
                        if neighbour.get("output"):
                            outcome = str(neighbour["output"])
                            break
                blocked = (
                    "permission denied" in outcome.lower()
                    or "not found" in outcome.lower()
                    or "Exit code: 1" in outcome
                    or ("Exit code: 127" in outcome)
                )
                for tool in tools:
                    if tool.strip() in command.lower():
                        hits.append(
                            {
                                "tool": tool.strip(),
                                "action": event.get("action_name"),
                                "command": command[:200],
                                "blocked": blocked,
                            }
                        )
                        break
        succeeded = [h for h in hits if not h["blocked"]]
        blocked = [h for h in hits if h["blocked"]]

        def _tally(entries):
            out: dict = {}
            for entry in entries:
                out[entry["tool"]] = out.get(entry["tool"], 0) + 1
            return out

        by_tool = _tally(succeeded)
        if succeeded:
            verdict = f"VIOLATION — the reference was analysed as a file and the analysis succeeded ({', '.join((f'{k}×{v}' for (k, v) in sorted(by_tool.items())))}). Any score from this run is not comparable."
        elif blocked:
            verdict = f"clean: {len(blocked)} attempt(s) to analyse the reference were refused by the environment ({', '.join((f'{k}×{v}' for (k, v) in sorted(_tally(blocked).items())))}) and returned nothing"
        else:
            verdict = "clean: no binary analysis of the reference"
        return {
            "checked": True,
            "clean": not succeeded,
            "verdict": verdict,
            "by_tool": by_tool,
            "blocked_attempts": _tally(blocked),
            "examples": [h["command"] for h in succeeded[:5]],
            "total": len(succeeded),
        }

    def _has_resumable_work(self, workspace_dir: str) -> bool:
        """True when a session workspace holds a prior reconstruction worth continuing from —
        a committed solution beyond the shipped initial commit (so `git archive HEAD` would
        yield source), a build script, and the preserved reference binary that is the oracle.
        Without the reference on disk there is nothing to reconstruct against, so a resume that
        found it missing would be worse than a clean re-seed and is declined here."""
        if not os.path.isdir(workspace_dir):
            return False
        reference = os.path.join(workspace_dir, self.reference_copy)
        compile_sh = os.path.join(workspace_dir, "compile.sh")
        if not (os.path.exists(reference) and os.path.exists(compile_sh)):
            return False
        count = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", workspace_dir, "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
        )
        try:
            return int(count.stdout.strip() or 0) > 1
        except ValueError:
            return False

    async def _seed_workspace(self, image_ref: str, destination: str) -> None:
        """Copy the image's `/workspace` into `destination` so it can be mounted back in.

        Bind-mounting a directory over `/workspace` would otherwise hide what the image ships
        there — the documentation that *is* the task specification, the reference binary, the
        git repository — leaving the agent an empty room. Seeding first and mounting second
        gives the same contents *and* a directory that can be watched while the run happens,
        instead of a workspace that only becomes visible when the run is over and its tarball
        lands.

        Copied by a throwaway container rather than `docker cp`: it runs as root inside the
        image, so `cp -a` reproduces modes and ownership exactly. That matters for one file —
        the reference binary is mode `---x--x--x`, executable but deliberately not readable,
        and a copy that widened it would hand the agent the bytes the benchmark withholds.

        A pre-existing directory is emptied first, again from inside a container: the previous
        run's files belong to root, so the user this launcher runs as cannot remove them.
        Mixing them into a fresh run would be worse than either — last run's source would look
        like this run's work.
        """
        os.makedirs(destination, exist_ok=True)
        container_name = (
            "agentevolver-seed-"
            + hashlib.sha256(os.path.abspath(destination).encode()).hexdigest()[:20]
        )
        script = (
            "set -eu; rm -rf /seed/..?* /seed/.[!.]* /seed/*; cp -a /workspace/. /seed/; "
            + "".join(
                f"grep -qxF {shlex.quote(entry)} /seed/.gitignore 2>/dev/null || echo {shlex.quote(entry)} >> /seed/.gitignore; "
                for entry in self.scaffolding
            )
            + f"chown {self.container_user} /seed/.gitignore"
        )
        try:
            await self._command(["docker", "rm", "-f", container_name], 60)
            result = await self._command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    container_name,
                    "--entrypoint",
                    "sh",
                    "-v",
                    f"{destination}:/seed",
                    image_ref,
                    "-c",
                    script,
                ],
                600,
            )
        finally:
            with contextlib.suppress(Exception):
                await self._command(["docker", "rm", "-f", container_name], 60)

        if result.returncode != 0:
            raise RuntimeError(
                f"could not seed the workspace from {image_ref}: {(result.stderr or result.stdout).strip()[:300]}"
            )

    async def _prepare(self, task, ctx, options):
        record = self._instances.get(task.task_id)
        if record is None:
            raise ValueError("unknown benchmark task")
        ctx.image = f"{record.get('image_name', '')}:task_cleanroom_v6"
        await self._prepare_runtime_mounts(options)
        ctx.resumed = bool(options.get("resume")) and self._has_resumable_work(ctx.workspace_dir)
        if not ctx.resumed:
            await self._seed_workspace(ctx.image, ctx.workspace_dir)
        ctx.permissions_changed = True
        await self._grant_workspace(
            ctx.session_dir, protected=("workspace/executable", "workspace/" + self.reference_copy)
        )
        await self._acquire_task_sandbox(ctx, options)

    async def _collect_submission(self, task, ctx, output):
        info = await self._blocking(
            self._package_submission, ctx.workspace_dir, os.path.dirname(ctx.submission_path)
        )
        path = str(path_manager.under(os.path.dirname(ctx.submission_path), P.PROJECT_SUBMISSION))
        with open(path, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        audit = self._audit_reference_binary(
            str(path_manager.under(ctx.session_dir, P.PROJECT_LOG))
        )
        return {
            "protocol": self.submission_protocol,
            "instance_id": task.task_id,
            "path": path,
            "sha256": digest,
            "agent_result": {
                **(output or {}),
                "submission": info,
                "reference_audit": audit,
                "egress_audit": ctx.egress_audit,
            },
        }

    def _validate_submission(self, task, submission):
        if (
            submission.get("instance_id") != task.task_id
            or not submission.get("path")
            or submission.get("protocol", self.submission_protocol) != self.submission_protocol
        ):
            raise ValueError("invalid frozen archive identity")
        with open(submission["path"], "rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != submission.get("sha256"):
                raise ValueError("invalid frozen archive digest")

    def _apply_submission(self, task, submission):
        task.result = submission

    async def _cleanup_task(self, ctx, *, reclaim=False):
        await self._stop_task_resources(ctx)

    def _historical_task(self, record):
        grade = record.get("final_grade")
        if isinstance(grade, dict) and record.get("instance_id"):
            score = grade.get("score")
            evaluation = EvaluationResult.from_report(grade, score=score)
            return Task(
                task_id=record["instance_id"],
                evaluation=evaluation,
                score=evaluation.score,
                extra={"spend": record.get("spend", {}), "historical": True},
            )
        return super()._historical_task(record)

    def __init__(
        self,
        base_dir: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(base_dir=base_dir, start=start, end=end, **kwargs)

    async def _initialize(self):
        # Created on first use, not at construction: the registry builds every
        # benchmark at startup, before any session is bound, so doing it in
        # __init__ scaffolded empty directories under the unbound root.
        os.makedirs(self.base_dir, exist_ok=True)
        # Ensure the test blobs exist locally (download from HF on first use), then
        # point the official evaluator at them so it runs fully offline.
        from agentevolver.benchmark.utils import ensure_dataset

        blob_dir = ensure_dataset(os.path.basename(self.path), self.hf_repo_id)
        os.environ["PROGRAMBENCH_BLOB_DIR"] = blob_dir

        from agentevolver.data.programbench import ProgramBenchDataset

        dataset = ProgramBenchDataset()
        self._instances = dataset.instances
        self._data_records = self._apply_slice(dataset.data)

    async def _step(self) -> Optional[Task]:
        if self._index >= len(self._data_records):
            return None

        record = self._data_records[self._index]
        self._index += 1

        instance_id = record["instance_id"]
        repository = record.get("repository", "")
        commit = record.get("commit", "")
        language = record.get("language", "")
        image_name = record.get("image_name", "")

        question = dedent(f"""
            Reconstruct the program `{repository}` (language: {language}).

            A compiled binary and its documentation are available in your sandbox
            (task image: `{image_name}`, commit `{commit}`). Implement a complete
            codebase that reproduces the program's behavior. Produce the full source
            tree as your final answer.
        """)

        extra = {
            "instance_id": instance_id,
            "repository": repository,
            "commit": commit,
            "language": language,
            "difficulty": record.get("difficulty"),
            "image_name": image_name,
        }

        return Task(
            task_id=instance_id,
            input=question,
            system_prompt=self.system_prompt,
            ground_truth=None,  # ground truth is the hidden test suite, applied during eval
            extra=extra,
        )

    def _prepare_submission(self, task_id: str, result: Any) -> str:
        """Copy an immutable, host-frozen archive into an independent grading attempt."""
        if not isinstance(result, dict) or not result.get("path") or not result.get("sha256"):
            raise ValueError("a frozen archive and its SHA-256 are required")
        if result.get("instance_id", task_id) != task_id:
            raise ValueError("submission task mismatch")
        src_path = result["path"]

        def digest(path):
            with open(path, "rb") as handle:
                return hashlib.file_digest(handle, "sha256").hexdigest()

        if digest(src_path) != result["sha256"]:
            raise ValueError("submission digest mismatch")
        runs = path_manager.resolve_under(self.base_dir, "eval_runs")
        os.makedirs(runs, exist_ok=True)
        log_root = tempfile.mkdtemp(prefix="grade-", dir=runs)
        inst_dir = path_manager.resolve_under(log_root, task_id)
        os.makedirs(inst_dir)
        submission = path_manager.under(inst_dir, P.PROJECT_SUBMISSION)
        shutil.copyfile(src_path, submission)
        if digest(submission) != result["sha256"]:
            raise ValueError("submission changed during collection")
        return log_root

    def _programbench_cli(self) -> Optional[str]:
        exe = os.path.join(os.path.dirname(sys.executable), "programbench")
        if os.path.exists(exe):
            return exe
        return shutil.which("programbench")

    async def _eval(self, task: Task) -> Task:
        """One final host-side evaluation. Errors remain unscored and never reach a solver."""
        report = {}
        score = None
        try:
            instance = self._instances.get(task.task_id)
            if instance is None:
                raise ValueError("unknown benchmark task")
            log_root = self._prepare_submission(task.task_id, task.result)
            cli = self._programbench_cli()
            if not cli:
                raise RuntimeError("programbench CLI not found")
            import re

            process = await asyncio.create_subprocess_exec(
                cli,
                "eval",
                log_root,
                "--filter",
                f"^{re.escape(task.task_id)}$",
                "--branch-workers",
                str(self.branch_workers),
                "--docker-cpus",
                str(self.docker_cpus),
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), self.eval_timeout)
            except BaseException:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                raise
            for name, data in (("stdout.log", stdout), ("stderr.log", stderr)):
                with open(path_manager.resolve_under(log_root, name), "wb") as handle:
                    handle.write(data)
            inst_dir = path_manager.resolve_under(log_root, task.task_id)
            eval_json = path_manager.resolve_under(inst_dir, f"{task.task_id}.eval.json")
            if process.returncode != 0:
                raise RuntimeError(
                    f"programbench exited {process.returncode}; evidence: {log_root}"
                )
            with open(eval_json, encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("error_code"):
                report = {
                    "error_code": raw["error_code"],
                    "error_details": raw.get("error_details"),
                    "eval_json": str(eval_json),
                }
            else:
                from programbench.submission import score_instance, test_results_map

                tests = test_results_map(eval_json, instance)
                if not tests:
                    raise RuntimeError("the grader produced no scored test results")
                score = score_instance(eval_json, instance)
                passed = sum(bool(value) for value in tests.values())
                report = {
                    "resolved": score >= 1.0,
                    "passed_tests": passed,
                    "total_tests": len(tests),
                    "eval_json": str(eval_json),
                }
        except Exception as error:
            report = {"error_code": "evaluation_failed", "error_details": str(error)}
        task.evaluation = EvaluationResult.from_report(report, score=score)
        task.score = task.evaluation.score
        return task

    async def _stats(self) -> Stats:
        total = len(self._data_records)
        scored = [task for task in self._tasks if task.score is not None]
        attempted = len(scored)
        # A task is "resolved" only when all (non-ignored) tests pass.
        resolved = sum(1 for r in self._tasks if r.score and r.score >= 1.0)
        mean_score = sum(r.score or 0.0 for r in scored) / attempted if attempted > 0 else 0.0

        task_times = {r.task_id: r.time for r in self._tasks if r.time is not None}
        avg_time = sum(task_times.values()) / len(task_times) if task_times else 0.0

        return Stats(
            accuracy=mean_score,  # mean fraction of tests passed across tasks
            total=total,
            correct=resolved,
            wrong=attempted - resolved,
            times=task_times,
            average_time=avg_time,
            extra={
                "evaluation_errors": len(self._tasks) - attempted,
                "resolved": resolved,
                "resolved_rate": resolved / attempted if attempted > 0 else 0.0,
                "mean_test_pass_rate": mean_score,
            },
        )
