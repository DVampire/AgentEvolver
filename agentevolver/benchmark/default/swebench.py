"""SWE-bench datasets and host-only final evaluators; never exposed as agent tools."""

import os
import asyncio
import ast
import contextlib
import hashlib
import inspect
import json
import re
import shlex
import tempfile
import random
import signal
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from pydantic import ConfigDict, Field, PrivateAttr

from agentevolver.benchmark.types import Benchmark, Task, Stats, EvaluationResult
from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.registry import BENCHMARK
from agentevolver.utils import dedent
from agentevolver.utils.file_utils import atomic_write_text

SYSTEM_PROMPT = dedent("""
    You are an expert software engineer working inside a real repository at a known
    commit. You are given one issue report and the repository it belongs to.

    Write the source change that resolves the issue. A hidden test suite decides the
    outcome: some tests currently fail and must pass after your change, and the tests that
    already pass must keep passing. You cannot see either list.

    Change only what the issue calls for. A patch that resolves the issue and breaks
    something else scores nothing.

    Task:
""")


class SWEBenchBenchmark(Benchmark):
    """Shared loader for the SWE-bench family: rows in, GT-safe tasks out."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    #: Fields an agent's container may see. Everything else in a row is the answer key
    #: (`patch`, `test_patch`, `fail_to_pass`, `pass_to_pass`) or host-side scoring
    #: metadata. Subclasses set this; the base deliberately has no default, so a new
    #: variant cannot inherit another benchmark's idea of what is safe to reveal.
    safe_fields: Tuple[str, ...] = Field(default=())

    system_prompt: Optional[str] = Field(default=SYSTEM_PROMPT)

    _data_records: List[Dict] = PrivateAttr(default_factory=list)

    container_workspace: str = "/workspace"
    image_workspace: str = "/app"
    submission_protocol: str = "swe-pro-final-only-v2"
    pull_attempts: int = 4
    pull_timeout: int = 2700
    seed_timeout: int = 2700

    def _collect_patch(self, workspace: str, base_commit: str) -> str:
        """The agent's change as a git patch: everything the tree differs from base_commit.

        Both committed and uncommitted edits are captured — the agent is told to commit, but a
        forgotten commit must not lose the work. Returns '' when the tree is unchanged. This is
        the raw agent patch, exactly like an SWE-agent ``.pred``; binary noise from the image's
        baked-in git quirks is dropped later by ``strip_binary_hunks`` at grade time, matching the
        official harness.
        """
        git_dir = os.path.join(workspace, ".git")

        def _git(*args, env=None):
            return subprocess.run(
                ["git", f"--git-dir={git_dir}", f"--work-tree={workspace}", *args],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )

        with tempfile.TemporaryDirectory(prefix="agentevolver-patch-index-") as tmp_dir:
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = os.path.join(tmp_dir, "index")
            object_dir = os.path.join(tmp_dir, "objects")
            os.makedirs(object_dir)
            env["GIT_OBJECT_DIRECTORY"] = object_dir
            env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.path.join(git_dir, "objects")
            initialized = _git("read-tree", "HEAD", env=env)
            if initialized.returncode != 0:
                raise RuntimeError(
                    f"git read-tree failed while collecting patch: {initialized.stderr.strip()}"
                )
            staged = _git("add", "-A", "-N", env=env)
            if staged.returncode != 0:
                raise RuntimeError(
                    f"git add -N failed while collecting patch: {staged.stderr.strip()}"
                )
            diff = _git("diff", "--binary", base_commit, env=env)
            if diff.returncode != 0:
                raise RuntimeError(f"git diff failed while collecting patch: {diff.stderr.strip()}")
            return diff.stdout

    def _reclaim_instance_disk(self, workspace_dir, image_ref_str) -> None:
        """Free one graded instance's disk: its seeded workspace and its (unique) image.

        Runs in a worker thread. The workspace is container-uid-owned, so its contents are
        removed through a throwaway root container before the (host-owned) top dir is dropped.
        The image's dockerhub_tag is unique to the instance, so ``docker rmi`` frees it without
        affecting any other in-flight instance. Best-effort — never raises.
        """
        if workspace_dir and os.path.isdir(workspace_dir):
            with contextlib.suppress(Exception):
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{workspace_dir}:/w",
                        "alpine",
                        "sh",
                        "-c",
                        "rm -rf /w/..?* /w/.[!.]* /w/* 2>/dev/null; true",
                    ],
                    capture_output=True,
                    timeout=300,
                )
            with contextlib.suppress(Exception):
                shutil.rmtree(workspace_dir, ignore_errors=True)
        if image_ref_str:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["docker", "rmi", "-f", image_ref_str], capture_output=True, timeout=180
                )

    async def _provision_command(
        self, argv: list[str], timeout: float
    ) -> subprocess.CompletedProcess:
        """Keep provisioning cancellable instead of leaving an uninterruptible worker thread."""
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            (stdout, stderr) = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        return subprocess.CompletedProcess(
            argv,
            process.returncode,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    async def _ensure_image(self, image_ref_str: str) -> None:
        """Ensure one task image is local, retrying transient registry failures."""
        present = await self._provision_command(["docker", "image", "inspect", image_ref_str], 60)
        if present.returncode == 0:
            return
        last = ""
        for attempt in range(1, self.pull_attempts + 1):
            try:
                pulled = await self._provision_command(
                    ["docker", "pull", image_ref_str], self.pull_timeout
                )
                if pulled.returncode == 0:
                    return
                last = (pulled.stderr or pulled.stdout).strip()
            except asyncio.TimeoutError as error:
                last = f"pull exceeded {self.pull_timeout}s: {error}"
            if attempt < self.pull_attempts:
                await asyncio.sleep(2**attempt + random.uniform(0.0, 1.0))
        raise RuntimeError(
            f"could not pull {image_ref_str} after {self.pull_attempts} attempts: {last[-1000:] or '(no Docker output)'}"
        )

    async def _seed_workspace_async(
        self, image_ref_str: str, base_commit: str, destination: str
    ) -> None:
        """Copy the image's /app into destination at base_commit, the way the grader starts.

        Done by a throwaway root container so modes/ownership copy exactly, then handed to the
        container uid so the agent can edit it. A pre-existing dir is emptied first (root-owned).

        The setup mirrors the official grader's tree — ``git reset --hard`` then ``git checkout``
        at base_commit, and *no* ``git clean``. It additionally removes every ref except a fresh
        benchmark-base branch and prunes unreachable objects. Some published instance images
        retain later upstream commits, including the gold fix; leaving those objects addressable
        lets an agent recover answer metadata or the complete patch with ``git show``. Keeping the
        base commit and its ancestors preserves normal repository history without exposing future
        commits. Remotes are removed as a second line of defence behind the network policy.

        ``git clean -fdx`` is deliberately avoided: these images carry a baked-in index quirk
        where a tracked, present file (e.g. ``test/files/toobig.jpg``) reads as deleted, and clean
        would wrongly remove it. Any binary noise that still lands in the diff is dropped by
        ``strip_binary_hunks`` at grade time, again matching the official harness
        (swe_bench_pro_eval.py: assemble_workspace_files).
        """
        os.makedirs(destination, exist_ok=True)
        if not base_commit:
            raise ValueError("base_commit is required to seed a GT-safe SWE-bench Pro workspace")
        await self._ensure_image(image_ref_str)
        container_name = (
            "agentevolver-seed-"
            + hashlib.sha256(os.path.abspath(destination).encode()).hexdigest()[:20]
        )
        quoted_base = shlex.quote(base_commit)
        reset = f"""git reset --hard {quoted_base}; git checkout -B benchmark-base {quoted_base}; git remote | while IFS= read -r remote; do git remote remove "$remote"; done; git for-each-ref --format='%(refname)' | while IFS= read -r ref; do [ "$ref" = refs/heads/benchmark-base ] || git update-ref -d "$ref"; done; git reflog expire --expire=now --all; git gc --prune=now; """
        script = f"set -eu; rm -rf /seed/..?* /seed/.[!.]* /seed/*; cp -a {self.image_workspace}/. /seed/; cd /seed; git config --global --add safe.directory /seed; {reset}chown -R {self.container_user} /seed"
        try:
            await self._provision_command(["docker", "rm", "-f", container_name], 60)
            result = await self._provision_command(
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
                    image_ref_str,
                    "-c",
                    script,
                ],
                self.seed_timeout,
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"seeding the workspace from {image_ref_str} exceeded {self.seed_timeout}s (copy + reset + gc of the checkout)."
            ) from e
        finally:
            with contextlib.suppress(Exception):
                await self._provision_command(["docker", "rm", "-f", container_name], 60)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not seed the workspace from {image_ref_str}: {(result.stderr or result.stdout).strip()[:300]}"
            )

    def _has_resumable_workspace(self, workspace_dir: str, base_commit: str) -> bool:
        """Whether ``workspace_dir`` is a usable checkout for an interrupted instance.

        A resume is intentionally coarse-grained: completed instances are skipped from the
        durable run ledger, while an in-flight instance reuses its checkout and starts a fresh
        agent turn over the existing edits.  Requiring both a valid HEAD and the benchmark base
        object avoids building on a half-copied or corrupted seed after a host failure.
        """
        git_dir = os.path.join(workspace_dir, ".git")
        if not base_commit or not os.path.isdir(git_dir):
            return False

        def _git(*items):
            return subprocess.run(
                ["git", f"--git-dir={git_dir}", f"--work-tree={workspace_dir}", *items],
                capture_output=True,
                text=True,
                timeout=30,
            )

        try:
            head = _git("rev-parse", "--verify", "HEAD^{commit}")
            base = _git("cat-file", "-e", f"{base_commit}^{{commit}}")
        except (OSError, subprocess.TimeoutExpired):
            return False
        return head.returncode == 0 and base.returncode == 0

    def _instance(self, task: Task) -> Dict:
        row = next((r for r in self._data_records if r["instance_id"] == task.task_id), None)
        if row is None:
            raise ValueError("unknown benchmark task")
        return row

    def _image_ref(self, row: Dict) -> str:
        raise NotImplementedError

    async def _prepare(self, task, ctx, options):
        row = self._instance(task)
        ctx.image = self._image_ref(row)
        await self._prepare_runtime_mounts(options)
        ctx.resumed = bool(options.get("resume")) and self._has_resumable_workspace(
            ctx.workspace_dir, row["base_commit"]
        )
        if ctx.resumed:
            await self._ensure_image(ctx.image)
        else:
            await self._seed_workspace_async(ctx.image, row["base_commit"], ctx.workspace_dir)
        ctx.permissions_changed = True
        await self._grant_workspace(
            ctx.workspace_dir if options.get("agent_on_host", True) else ctx.session_dir
        )
        await self._acquire_task_sandbox(ctx, options)

    async def _collect_submission(self, task, ctx, output):
        row = self._instance(task)
        patch = await self._blocking(self._collect_patch, ctx.workspace_dir, row["base_commit"])
        patch_path = str(path_manager.under(ctx.session_dir, P.PROJECT_PATCH))
        atomic_write_text(patch_path, patch)
        output = {
            **(output or {}),
            "patch_path": patch_path,
            "patch_bytes": len(patch),
            "has_patch": bool(patch.strip()),
        }
        return {
            "protocol": self.submission_protocol,
            "instance_id": task.task_id,
            "base_commit": row["base_commit"],
            "patch": patch,
            "sha256": hashlib.sha256(patch.encode()).hexdigest(),
            "agent_result": {**(output or {}), "egress_audit": ctx.egress_audit},
        }

    def _validate_submission(self, task, submission):
        row = self._instance(task)
        if (
            submission.get("protocol") != self.submission_protocol
            or submission.get("instance_id") != task.task_id
            or submission.get("base_commit") != row["base_commit"]
            or not isinstance(submission.get("patch"), str)
            or not isinstance(submission.get("agent_result"), dict)
            or submission.get("sha256") != hashlib.sha256(submission["patch"].encode()).hexdigest()
        ):
            raise ValueError("invalid frozen submission identity or digest")

    def _apply_submission(self, task, submission):
        task.result = submission

    async def _cleanup_task(self, ctx, *, reclaim=False):
        await self._stop_task_resources(ctx)
        terminal = any(
            t.task_id == ctx.task_id and t.evaluation and t.evaluation.status != "error"
            for t in self._tasks
        )
        if reclaim and terminal:
            await self._blocking(self._reclaim_instance_disk, ctx.workspace_dir, ctx.image)

    def _historical_task(self, record):
        grade = record.get("final_grade")
        if isinstance(grade, dict) and record.get("instance_id"):
            evaluation = EvaluationResult.from_report(grade)
            return Task(
                task_id=record["instance_id"],
                score=evaluation.score,
                evaluation=evaluation,
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
        # Built on first use rather than at construction: the registry instantiates every
        # benchmark at startup, before a session is bound, so doing this in __init__
        # scaffolded empty directories under the unbound root.
        from agentevolver.benchmark.utils import ensure_dataset, _dir_has_content
        from datasets import load_dataset

        os.makedirs(self.base_dir, exist_ok=True)
        local = (
            self.path
            if os.path.isfile(self.path) or _dir_has_content(self.path)
            else ensure_dataset(os.path.basename(self.path), self.hf_repo_id)
        )
        if os.path.isfile(local) and local.endswith(".parquet"):
            dataset = load_dataset("parquet", data_files={self.split: local}, split=self.split)
        else:
            dataset = load_dataset(local, split=self.split)
        self._data_records = self._apply_slice(list(dataset))
        logger.info(f"| 📚 {self.name}: {len(self._data_records)} instance(s) from {local}")

    def _task_payload(self, row: Dict) -> Dict:
        """The subset of a row a container may see — never the gold patch or the tests."""
        return {key: row.get(key, "") for key in self.safe_fields}

    async def _step(self) -> Optional[Task]:
        if self._index >= len(self._data_records):
            return None
        record = self._data_records[self._index]
        self._index += 1
        return Task(
            task_id=str(record.get("instance_id", f"{self._index:04d}")),
            input=str(record.get("problem_statement", "")),
            system_prompt=self.system_prompt,
            # The resolution oracle is the hidden suite, which only the host-side grader
            # may consult. Carrying it on the Task would put it one attribute access away
            # from whatever renders a prompt.
            ground_truth=None,
            extra={
                **self.task_payload(record),
                **{key: record[key] for key in ("image", "dockerhub_tag") if key in record},
            },
        )

    async def _eval(self, task: Task) -> Task:
        row = next(
            (item for item in self._data_records if item["instance_id"] == task.task_id), None
        )
        submission = task.result
        try:
            if row is None:
                raise ValueError("unknown benchmark task")
            if not isinstance(submission, dict) or not isinstance(submission.get("patch"), str):
                raise ValueError("a frozen patch submission is required")
            if (
                submission.get("instance_id") != task.task_id
                or submission.get("base_commit") != row["base_commit"]
                or submission.get("sha256")
                != hashlib.sha256(submission["patch"].encode()).hexdigest()
            ):
                raise ValueError("submission identity or digest mismatch")
            report = await self._evaluate(row, submission["patch"], task.extra or {})
        except Exception as error:
            report = {"error_code": "evaluation_failed", "error_details": str(error)}
        task.evaluation = EvaluationResult.from_report(report)
        task.score = task.evaluation.score
        return task

    async def _stats(self) -> Stats:
        return Stats(total=len(self._data_records))

    async def _evaluate(self, row: Dict, patch: str, options: Dict) -> Dict:
        raise NotImplementedError


@BENCHMARK.register_module(force=True)
class SWEBenchVerifiedBenchmark(SWEBenchBenchmark):
    """SWE-bench Verified — 500 human-validated Python issues from 12 repositories."""

    image_workspace: str = "/testbed"
    container_workspace: str = "/testbed"
    name: str = Field(default="swebench_verified")
    path: str = Field(default="datasets/SWE-bench_Verified")
    hf_repo_id: str = Field(default="SWE-bench/SWE-bench_Verified")
    # Verified carries no requirements/interface: the issue text is the whole spec.
    safe_fields: Tuple[str, ...] = Field(
        default=("instance_id", "repo", "base_commit", "problem_statement")
    )

    def _image_ref(self, row: Dict) -> str:
        return row["image"]

    async def _evaluate(self, row: Dict, patch: str, options: Dict) -> Dict:
        return await _grade_verified(options["workspace_dir"], row, patch=patch)


class SWEBenchProTaskRepairs:
    """Reviewed task-data corrections, applied only in the final grading container.

    Entries pin the dataset identity, injected test revision and exact fixture bytes.
    Unknown tasks are untouched. Adding an entry requires a reproduced setup defect
    and a check that an incorrect implementation still fails after the repair.
    """

    VERSION = "swe-pro-task-repairs-v1"
    REPAIRS = {
        "instance_flipt-io__flipt-756f00f79ba8abf9fe53f3c6c818123b42eb7355": {
            "id": "flipt-config-test-fixtures-v1",
            "repo": "flipt-io/flipt",
            "base_commit": "266e5e143e87519047b9be3202a0aba273b83de3",
            "test_revision": "756f00f79ba8abf9fe53f3c6c818123b42eb7355",
            "test_files": ["internal/config/config_test.go"],
            "reason": "Injected TestLoad requires its matching added and updated YAML fixtures.",
            "fixtures": {
                "internal/config/testdata/advanced.yml":
                    "8cc7db5d89c0ffe2eefef5aa488b3cad0459cb8a469d416673ff3ef8f3671c11",
                "internal/config/testdata/deprecated/ui_disabled.yml":
                    "873e0e39f2da7690e5cfdc00eb69c3046ce28c20123f7ffe8aeeafba17f0983c",
            },
        },
    }

    @classmethod
    def fingerprint(cls, grader_fingerprint: str) -> str:
        payload = json.dumps({"grader": grader_fingerprint, "version": cls.VERSION,
                              "repairs": cls.REPAIRS, "implementation": inspect.getsource(cls)},
                             sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def prepare(cls, row: Dict) -> Tuple[Dict, Optional[Dict]]:
        """Return detached grading metadata; never modify the dataset or agent patch."""
        repair = cls.REPAIRS.get(row["instance_id"])
        if repair is None:
            return dict(row), None
        from pathlib import PurePosixPath

        for key in ("repo", "base_commit"):
            if row.get(key) != repair[key]:
                raise ValueError(f"Task repair {repair['id']}: changed {key}; review required")
        lines = row.get("before_repo_set_cmd", "").strip().splitlines()
        expected = ["git", "checkout", repair["test_revision"], "--", *repair["test_files"]]
        if not lines or shlex.split(lines[-1]) != expected:
            raise ValueError(f"Task repair {repair['id']}: changed test checkout; review required")
        roots = {PurePosixPath(path).parent / "testdata" for path in repair["test_files"]}
        for path, digest in repair["fixtures"].items():
            relative = PurePosixPath(path)
            if (relative.is_absolute() or ".." in relative.parts
                    or not any(relative.is_relative_to(root) for root in roots)
                    or relative.suffix not in {".json", ".yaml", ".yml", ".txt", ".csv", ".tsv", ".xml", ".golden"}
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError(f"Task repair {repair['id']}: invalid fixture manifest")
        metadata = {"catalog_version": cls.VERSION, "task_id": row["instance_id"], **repair}
        checksums = [f"{digest}  {path}" for path, digest in repair["fixtures"].items()]
        # Upstream consumes only the LAST line of before_repo_set_cmd. Keep the
        # checkout, content verification and acknowledgement in that single line.
        lines[-1] = (
            shlex.join([*expected, *repair["fixtures"]])
            + " && printf '%s\\n' " + shlex.join(checksums)
            + " | sha256sum --check --status"
            + " && printf '%s\\n' " + shlex.quote(json.dumps(metadata, sort_keys=True))
            + " > /workspace/task_repair.json || exit 86"
        )
        return {**row, "before_repo_set_cmd": "\n".join(lines)}, metadata


@BENCHMARK.register_module(force=True)
class SWEBenchProBenchmark(SWEBenchBenchmark):
    """SWE-bench Pro — 731 issues across Python, Go, JS and TS, with longer patches."""

    name: str = Field(default="swebench_pro")
    path: str = Field(default="datasets/SWE-bench_Pro")
    hf_repo_id: str = Field(default="ScaleAI/SWE-bench_Pro")

    def _image_ref(self, row: Dict) -> str:
        return pro_image_ref(row)

    async def _evaluation_info(self, options: Dict) -> Dict:
        if not options:
            return {}
        profile = options.get("grader_profile", "official")
        if profile not in {"official", "diagnostic"}:
            raise ValueError(f"Unknown SWE Pro grader profile: {profile}")
        repo = os.path.abspath(options["grader_repo"])
        if not os.path.isfile(os.path.join(repo, "swe_bench_pro_eval.py")):
            raise ValueError(f"grader repo not found: {repo}")
        fingerprint = _grader_fingerprint(repo)
        if profile == "official":
            fingerprint = SWEBenchProTaskRepairs.fingerprint(fingerprint)
        return {
            "grader_profile": profile,
            "grader_implementation": (
                OFFICIAL_PRO_GRADER_PROTOCOL if profile == "official" else "swe-pro-diagnostic-v1"
            ),
            "grader_fingerprint": fingerprint,
        }
    # Pro adds a written requirements list and an interface spec to the issue text.
    safe_fields: Tuple[str, ...] = Field(
        default=("instance_id", "repo", "repo_language", "base_commit",
                 "problem_statement", "requirements", "interface")
    )

    async def _evaluate(self, row: Dict, patch: str, options: Dict) -> Dict:
        return await _grade_pro(options["workspace_dir"], row, options["grader_repo"],
                                patch=patch, profile=options.get("grader_profile", "official"))


_GRADE_TIMEOUT_SECONDS = 1800
_GRADER_WORKERS = 2
_GRADER_EVIDENCE = ("stdout.log", "stderr.log", "output.json", "status.json", "fixtures.json")
OFFICIAL_PRO_GRADER_PROTOCOL = "swe-pro-upstream-local-docker-v1"


def pro_image_ref(row: dict) -> str:
    return f"jefzda/sweap-images:{row['dockerhub_tag']}"


def _official_pro_image_ref(row: dict) -> str:
    """Port of upstream helper_code/image_uri.py::get_dockerhub_image_uri."""
    uid, repo = row["instance_id"], row["repo"]
    repo_base, repo_name = repo.lower().split("/")
    hsh = uid.replace("instance_", "")
    if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name = "element-web"
    elif "element-hq" in repo.lower() and "element-web" in repo.lower():
        repo_name = "element"
        if hsh.endswith("-vnan"):
            hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]
    return f"jefzda/sweap-images:{f'{repo_base}.{repo_name}-{hsh}'[:128]}"


def _official_pro_report(results, row: dict) -> dict:
    """Upstream main's set-membership score, without diagnostic overrides.

    A wrapper reported PASSED by the upstream parser remains PASSED here, even if
    its log mentions skipped subtests or another package failed compilation.
    Such evidence is retained for auditing, not used to change the official score.
    """
    available = isinstance(results, dict) and isinstance(results.get("tests"), list)
    passed = {test["name"] for test in results["tests"] if test["status"] == "PASSED"} if available else set()
    f2p, p2p = set(_as_list(row, "fail_to_pass")), set(_as_list(row, "pass_to_pass"))
    return {
        "fail_to_pass_resolved": len(f2p & passed), "fail_to_pass_total": len(f2p),
        "pass_to_pass_preserved": len(p2p & passed), "pass_to_pass_total": len(p2p),
        "resolved": available and (f2p | p2p) <= passed,
        "official_output_available": available,
        "grader_profile": "official", "grader_protocol": OFFICIAL_PRO_GRADER_PROTOCOL,
    }


def _run_official_pro(workspace_dir: str, row: dict, grader_repo: str, patch: str) -> dict:
    """Upstream eval_with_docker migrated into the benchmark implementation.

    Reference: scaleapi/SWE-bench_Pro-os, ca10a60a5fcae51e6948ffe1485d4153d421e6c5.
    Reviewed task-data repairs are applied automatically and labelled official_repaired.
    Image selection, pull/local fallback, bind mount,
    entrypoint, scripts, inherited image environment, container wait and scoring match
    the upstream local-Docker path. No launcher process or second result ledger.
    """
    import platform
    from pathlib import Path

    evidence_root = Path(workspace_dir).resolve().parent / "evaluation"
    evidence_root.mkdir(parents=True, exist_ok=True)
    artifact = Path(tempfile.mkdtemp(prefix="official-", dir=evidence_root))
    mounted = artifact / "workspace"
    mounted.mkdir()
    results = None
    repair = None
    repair_verified = False
    repair_required = row["instance_id"] in SWEBenchProTaskRepairs.REPAIRS
    try:
        import docker

        row, repair = SWEBenchProTaskRepairs.prepare(row)
        if repair:
            (artifact / "task_repair_expected.json").write_text(
                json.dumps(repair, indent=2), encoding="utf-8")
        iid = row["instance_id"]
        files = {
            "patch.diff": strip_binary_hunks(patch),
            "run_script.sh": _load_script(grader_repo, iid, "run_script.sh"),
            "parser.py": _load_script(grader_repo, iid, "parser.py"),
            "entryscript.sh": _build_entryscript(row, grader_repo, profile="official"),
        }
        for name, content in files.items():
            (mounted / name).write_text(content, encoding="utf-8")
        (artifact / "entryscript.sh").write_text(files["entryscript.sh"], encoding="utf-8")
        (artifact / "submitted.patch").write_text(patch, encoding="utf-8")
        image = _official_pro_image_ref(row)
        docker_platform = "linux/amd64" if platform.machine().lower() in {"arm64", "aarch64"} else None
        client = docker.from_env()
        try:
            try:
                client.images.pull(image, **({"platform": docker_platform} if docker_platform else {}))
            except Exception:
                client.images.get(image)  # Same fallback to an existing image as upstream.
            kwargs = {
                "volumes": {str(mounted): {"bind": "/workspace", "mode": "rw"}},
                "detach": True, "remove": True, "entrypoint": "/bin/bash",
                "command": ["-c", "bash /workspace/entryscript.sh"],
            }
            if docker_platform:
                kwargs["platform"] = docker_platform
            container = client.containers.run(image, **kwargs)
            result = container.wait()
            # Upstream still reads output.json after a nonzero container exit.
            (artifact / "execution.json").write_text(json.dumps(result), encoding="utf-8")
            for name in ("stdout.log", "stderr.log"):
                source = mounted / name
                (artifact / name).write_text(source.read_text() if source.exists() else "", encoding="utf-8")
            if repair:
                acknowledgement = json.loads((mounted / "task_repair.json").read_text(encoding="utf-8"))
                if acknowledgement != repair:
                    raise ValueError("Task repair acknowledgement does not match the pinned manifest")
                repair_verified = True
                (artifact / "task_repair.json").write_text(
                    json.dumps(acknowledgement, indent=2), encoding="utf-8")
            results = json.loads((mounted / "output.json").read_text(encoding="utf-8"))
            (artifact / "output.json").write_text(json.dumps(results), encoding="utf-8")
        finally:
            client.close()
        report = _official_pro_report(results, row)
    except Exception as error:
        # Upstream main scores evaluator exceptions/missing output as False.
        (artifact / "exception.log").write_text(f"{type(error).__name__}: {error}", encoding="utf-8")
        report = _official_pro_report(None, row)
        if repair_required and not repair_verified:
            report.update(error_code="test_data_repair_failed", error_details=str(error))
    if repair_required:
        report.update(grader_profile="official_repaired", task_repair_applied=repair_verified,
                      task_repair_id=SWEBenchProTaskRepairs.REPAIRS[row["instance_id"]]["id"],
                      task_repair_version=SWEBenchProTaskRepairs.VERSION)
    report["evidence_path"] = str(artifact)
    (artifact / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def strip_binary_hunks(patch: str) -> str:
    """Drop binary diff sections from a git patch — verbatim from the official harness.

    swe_bench_pro_eval.py:assemble_workspace_files runs this before ``git apply``, so the
    benchmark grades text changes only. We do the same so a binary hunk the agent's ``git
    diff`` picked up (notably these images' baked-in "deleted but present" test fixtures)
    cannot fail the whole apply and zero out an otherwise-correct patch.
    """
    if not patch:
        return patch
    sections = re.split(r'(?=^diff --git )', patch, flags=re.MULTILINE)
    kept: list = []
    for section in sections:
        if not section.strip():
            continue
        if re.search(r'^Binary files .* differ$', section, re.MULTILINE):
            continue
        if re.search(r'^GIT binary patch$', section, re.MULTILINE):
            continue
        kept.append(section)
    return "".join(kept)


def _as_list(row: dict, field: str) -> list:
    v = row.get(field, "")
    try:
        if not isinstance(v, str):
            return list(v or [])
        if not v.strip():
            return []
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            # SWE-bench Pro follows the official evaluator's Python-literal format for
            # a few rows whose test names are not valid JSON strings.
            parsed = ast.literal_eval(v)
        return list(parsed or [])
    except Exception:  # noqa: BLE001
        return []


def _load_script(grader_repo: str, instance_id: str, name: str) -> str:
    with open(os.path.join(grader_repo, "run_scripts", instance_id, name), encoding="utf-8") as h:
        return h.read()


def _argv_safe_run_script(script: str) -> str:
    """Make the common grader wrapper consume the argv we already parsed.

    Some generated scripts also treat a comma in their *first argument* as a legacy list
    separator. A real Go subtest name can itself contain a comma, so that heuristic changes
    the test identity and silently executes nothing. The dataset field is already a list;
    preserve its elements exactly.
    """
    legacy = '''if [[ "$1" == *","* ]]; then
    IFS=',' read -r -a TEST_FILES <<< "$1"
else
    TEST_FILES=("$@")
fi'''
    script = script.replace(legacy, 'TEST_FILES=("$@")')
    # ansible-test chooses its own xdist worker count, ignoring CPU quotas on some
    # images. Bound that inner parallelism independently from benchmark concurrency.
    return re.sub(r"(\bansible-test\s+units\b)(?![^\n]*--num-workers)",
                  rf"\1 --num-workers {_GRADER_WORKERS}", script)


def _restore_test_fixtures(repo: str, base: str, before: str, *, match_revision: bool = False) -> list[str]:
    """Restore Go test data in the diagnostic grader only, never reference code.

    Self-contained because this function also runs in the isolated grading image.
    By default only missing additions are restored. The diagnostic profile explicitly
    pairs added/modified data with the injected tests' revision; it is not an official score.
    Executable files, symlinks and production directories are always excluded.
    """
    import os
    import re
    import shlex
    import subprocess
    from pathlib import Path, PurePosixPath

    command = shlex.split(before)
    if len(command) < 5 or command[:2] != ["git", "checkout"] or command[3] != "--":
        return []
    revision = command[2]
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
        return []
    roots = sorted({str(PurePosixPath(p).parent / "testdata") for p in command[4:]
                    if p.endswith("_test.go") and not PurePosixPath(p).is_absolute()
                    and ".." not in PurePosixPath(p).parts})
    if not roots:
        return []

    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args])

    changes = "AM" if match_revision else "A"
    paths = git("diff", "--name-only", "-z", f"--diff-filter={changes}", base, revision, "--", *roots)
    restored = []
    allowed = {".json", ".yaml", ".yml", ".txt", ".csv", ".tsv", ".xml", ".golden"}
    for raw in paths.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        relative = PurePosixPath(path)
        target = Path(repo) / path
        if relative.is_absolute() or ".." in relative.parts or relative.suffix not in allowed:
            continue
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            continue
        if os.path.lexists(target) and (not match_revision or not target.is_file()):
            continue
        entry = git("ls-tree", revision, "--", path)
        if not entry.startswith(b"100644 blob "):
            continue
        if target.is_file() and target.read_bytes() == git("show", f"{revision}:{path}"):
            continue
        git("restore", "--source", revision, "--worktree", "--", path)
        restored.append(path)
    return restored


def _parser_with_fail_boundaries(script: str) -> str:
    """Keep Jest FAIL sections from being attributed to the preceding PASS file.

    A number of shipped parsers use PASS as the only file boundary. Jest prints a FAIL
    header in the same position, and a failed suite may still contain hundreds of passing
    tests. Parsing both headers preserves those passing results while the failed assertion
    remains absent/failed as before.
    """
    script = script.replace(
        'if line.startswith("PASS"):',
        'if line.startswith(("PASS", "FAIL")):',
    )
    return script.replace(
        'not lines[i].strip().startswith("PASS")',
        'not lines[i].strip().startswith(("PASS", "FAIL"))',
    )


def _build_entryscript(row: dict, grader_repo: str, *, profile: str = "diagnostic") -> str:
    """Mirror swe_bench_pro_eval.create_entryscript: ENV from the dockerfiles, then reset to
    base, apply the agent's patch, run before_repo_set_cmd (which checks out the resolution
    test files from the graded commit), run the selected tests, and parse to output.json.

    The test files are injected here, INSIDE this throwaway grading sandbox — never in the
    agent's sandbox — so the oracle stays out of the agent's reach.
    """
    iid = row["instance_id"]
    env_cmds = []
    for kind in ("base_dockerfile", "instance_dockerfile"):
        p = os.path.join(grader_repo, "dockerfiles", kind, iid, "Dockerfile")
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith("ENV"):
                    env_cmds.append(line.replace("ENV", "export", 1))
    before = row.get("before_repo_set_cmd", "").strip().split("\n")[-1]
    test_files = shlex.join(str(item) for item in _as_list(row, "selected_test_files_to_run"))
    base = row.get("base_commit", "")
    if profile == "official":
        # Preserve upstream invocation semantics, including its comma-joined selector.
        env = "\n".join(env_cmds)
        selected = ",".join(str(item) for item in _as_list(row, "selected_test_files_to_run"))
        return f"""
{env}
# apply patch
cd /app
git reset --hard {base}
git checkout {base}
git apply -v /workspace/patch.diff
{before}
# run test and save stdout and stderr to separate files
bash /workspace/run_script.sh {selected} > /workspace/stdout.log 2> /workspace/stderr.log
# run parsing script
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""
    return "\n".join(env_cmds) + f"""
set -e
stage=setup
test_exit=0
parser_exit=0
write_status() {{
    code=$?
    printf '{{"stage":"%s","exit_code":%s,"test_exit":%s,"parser_exit":%s}}' "$stage" "$code" "$test_exit" "$parser_exit" > /workspace/status.json
}}
trap write_status EXIT
export PYTEST_XDIST_AUTO_NUM_WORKERS={_GRADER_WORKERS}
export GOMAXPROCS={_GRADER_WORKERS}
export GOFLAGS="${{GOFLAGS:+$GOFLAGS }}-p={_GRADER_WORKERS}"
cd /app
git config --global --add safe.directory /app
git reset --hard {shlex.quote(base)}
git checkout {shlex.quote(base)}
stage=patch
git apply -v /workspace/patch.diff
stage=tests_restore
{before}
stage=fixtures_restore
python /workspace/restore_fixtures.py
stage=tests
bash /workspace/run_script.sh {test_files} > /workspace/stdout.log 2> /workspace/stderr.log || test_exit=$?
stage=parser
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json || parser_exit=$?
stage=complete
"""


def _counts(results, fail_to_pass: list, pass_to_pass: list) -> dict:
    """Per-test {name,status} → COUNTS ONLY (no names cross back to the agent)."""
    status = {}
    for t in (results if isinstance(results, list) else results.get("tests", [])):
        n = t.get("name")
        if n:
            status[n] = str(t.get("status", "")).upper()
    f2p = sum(1 for n in fail_to_pass if status.get(n) == "PASSED")
    p2p = sum(1 for n in pass_to_pass if status.get(n) == "PASSED")
    return {
        "fail_to_pass_resolved": f2p, "fail_to_pass_total": len(fail_to_pass),
        "pass_to_pass_preserved": p2p, "pass_to_pass_total": len(pass_to_pass),
        "resolved": (f2p == len(fail_to_pass) and p2p == len(pass_to_pass) and len(fail_to_pass) > 0),
    }


async def _save_grader_evidence(sbx, workspace_dir: str, run) -> dict:
    """Copy the grading container's own logs out before it is destroyed.

    The container is thrown away at the end of the round, so anything not copied here is
    gone. Failures are swallowed: evidence is what explains a score, never what decides it.
    """
    evidence = {"entry.log": (getattr(run, "stdout", "") or "") + (getattr(run, "stderr", "") or "")}
    evidence["execution.json"] = json.dumps({
        "success": getattr(run, "success", None),
        "exit_code": getattr(run, "exit_code", None),
        "error": getattr(run, "error", None),
    })
    for name in _GRADER_EVIDENCE:
        with contextlib.suppress(Exception):
            evidence[name] = await sbx.read_file(f"/workspace/{name}")
    dest = path_manager.under(os.path.dirname(workspace_dir), P.PROJECT_EVALUATION)
    with contextlib.suppress(Exception):
        os.makedirs(dest, exist_ok=True)
        numbers = [int(m.group(1)) for name in os.listdir(dest)
                   if (m := re.fullmatch(r"grader-(\d+)\.entry\.log", name))]
        seq = max(numbers, default=-1) + 1
        for name, data in evidence.items():
            with contextlib.suppress(Exception):
                with open(os.path.join(dest, f"grader-{seq}.{name}"), "w", encoding="utf-8") as h:
                    h.write(data if isinstance(data, str) else str(data))
    return evidence


def _grade_report(results, row: dict, evidence: dict, *, profile: str = "diagnostic") -> dict:
    """Classify this invocation from its own evidence, not another round's log tail.

    Failed assertions stay benchmark failures. A partial/empty parser result is not a
    valid score when setup, dependencies or the test runner prevented target execution.
    Grading evidence stays on the host and is never sent back to a solver.
    """
    counts = _counts(results, _as_list(row, "fail_to_pass"), _as_list(row, "pass_to_pass"))
    tests = results if isinstance(results, list) else results.get("tests", [])
    try:
        status = json.loads(evidence.get("status.json", "{}"))
        fixtures = json.loads(evidence.get("fixtures.json", "[]"))
    except (TypeError, ValueError):
        status, fixtures = {}, []
    text = "\n".join(str(evidence.get(name, "")) for name in ("stdout.log", "stderr.log"))
    entry = str(evidence.get("entry.log", ""))
    try:
        execution = json.loads(evidence.get("execution.json", "{}"))
    except (TypeError, ValueError):
        execution = {}
    error = None
    # Upstream's official shell has no `set -e`: parsing can succeed even after
    # git apply/reset failed. Never report that as a valid evaluation of the patch.
    if execution.get("success") is False or execution.get("exit_code") not in {None, 0}:
        error = ("grader_execution_failed", "the grading command failed or timed out; partial test results are not a final score")
    elif re.search(r"^error: (?:patch failed:|.*patch does not apply|.*already exists in working directory|No valid patches in input|corrupt patch at line|.*: No such file or directory)", entry, re.M):
        error = ("patch_apply_failed", "the frozen submission did not apply to the grading checkout")
    elif re.search(r"^fatal:|^error: (?:pathspec |reference is not a tree)", entry, re.M):
        error = ("checkout_failed", "the grading checkout or test restoration failed")
    elif status.get("exit_code") and status.get("stage") != "complete":
        error = (f"{status.get('stage', 'setup')}_failed", "the grader stopped before completing its test and parser stages")
    elif status.get("parser_exit"):
        error = ("parser_failed", "the test result parser exited unsuccessfully")
    elif ("INTERNALERROR>" in text or (not tests and re.search(
            r"Too many open files|can't start new thread|Resource temporarily unavailable", text))):
        error = ("test_runner_failed", "the test runner failed internally or exhausted host resources")
    elif not tests and re.search(r"(?:testdata|fixtures?)/[^\n]*[Nn]o such file or directory", text):
        error = ("test_fixture_missing", "required test data is missing in the grading checkout")
    elif ("[build failed]" in text or (not tests and re.search(
            r"undefined[: ]|unknown field |cannot find package|ModuleNotFoundError:|ImportError:", text))):
        error = ("test_build_failed", "test compilation or imports failed; patch correctness and test compatibility need separate review")
    else:
        executed = bool(tests) or bool(re.search(
            r"^=== RUN\s|^--- (?:PASS|FAIL):|\b\d+ (?:passed|failed)\b|[✓✕✔✖]", text, re.M))
        if not executed:
            code = "test_selection_failed" if "no tests to run" in text else "no_tests_executed"
            error = (code, "no test execution was observed in the grader output")
        elif not tests:
            error = ("test_results_missing", "tests ran but the parser produced no test results")
    if error:
        counts.update(error_code=error[0], error_details=error[1], resolved=False)
        # A failed test build does not establish an infrastructure fault: the patch may
        # omit an interface required by tests. Keep it unscored and explicitly uncertain.
        counts["failure_kind"] = (
            "test_compatibility" if error[0] in {"test_build_failed", "patch_apply_failed"} else
            "grading_setup" if error[0] in {
                "test_fixture_missing", "parser_failed", "test_selection_failed",
                "no_tests_executed", "test_results_missing", "checkout_failed",
            } else "evaluation"
        )
    elif not counts["resolved"]:
        counts["failure_kind"] = "test_failure"
    if not counts["resolved"] and re.search(
            r"(?:testdata|fixtures?)/[^\n]*[Nn]o such file or directory", text):
        # Negative tests can intentionally reference absent files. Preserve a diagnostic
        # hint, not an automatic fixture repair or a change to the official score.
        counts["diagnostic_notes"] = [
            "Missing fixture paths appear in the test log; check whether expected by a "
            "negative test or required by a failing test before attributing the failure."
        ]
    if fixtures:
        counts.update(fixture_files_restored=len(fixtures), leaderboard_comparable=False)
    counts.update(grader_protocol=f"swe-pro-{profile}-v1", grader_profile=profile)
    if profile == "diagnostic":
        counts["leaderboard_comparable"] = False
    return counts


async def _grade_pro(workspace_dir: str, row: dict, grader_repo: str, *, patch: str,
                      profile: str = "official") -> dict:
    """Grade a frozen submission; never collect a mutable workspace or call an agent."""
    if profile == "official":
        return await asyncio.to_thread(_run_official_pro, workspace_dir, row, grader_repo, patch)

    from agentevolver.sandbox import sandbox_manager

    instance_id = row["instance_id"]
    patch = strip_binary_hunks(patch)
    if not patch.strip():
        return {**_counts([], _as_list(row, "fail_to_pass"), _as_list(row, "pass_to_pass")),
                "resolved": False, "grader_note": "empty submission"}
    try:
        run_script = _load_script(grader_repo, instance_id, "run_script.sh")
        parser_py = _load_script(grader_repo, instance_id, "parser.py")
        if profile == "diagnostic":
            run_script = _argv_safe_run_script(run_script)
            parser_py = _parser_with_fail_boundaries(parser_py)
        elif profile != "official":
            raise ValueError(f"unknown grader profile: {profile}")
        entryscript = _build_entryscript(row, grader_repo, profile=profile)
        before = row.get("before_repo_set_cmd", "").strip().split("\n")[-1]
        fixture_script = (
            "from __future__ import annotations\n" + inspect.getsource(_restore_test_fixtures)
            + "\nimport json\n"
            + f"restored = _restore_test_fixtures('/app', {row.get('base_commit', '')!r}, {before!r}, match_revision=True)\n"
            + "with open('/workspace/fixtures.json', 'w') as f:\n    json.dump(restored, f)\n"
        ) if profile == "diagnostic" else ""
    except Exception as e:  # noqa: BLE001
        return {"error_code": "scripts_missing", "error_details": str(e)}

    # Retries and host-only reference audits must never attach to another run's grader.
    namespace = hashlib.sha256(os.path.realpath(workspace_dir).encode()).hexdigest()[:16]
    reuse = f"eval-{namespace}-{instance_id}"
    sbx = None
    try:
        # A separate, throwaway container from the SAME image the agent used. Root + FULLY
        # OPEN network (empty allow/deny) so the test setup (npm/pip/go install, redis) can
        # run; a fully-open policy needs no egress relay/forwarder (and thus no framework
        # mount). It is a grader, not the agent, so network here does not weaken the agent's
        # anti-cheat isolation — the agent's own sandbox stays network=False.
        sbx = await sandbox_manager.acquire(
            "docker", reuse_key=reuse, image=pro_image_ref(row), network=True,
            allow_hosts=[], deny_hosts=[], user="0:0", workdir="/app")
        for path, data in (("/workspace/patch.diff", patch),
                           ("/workspace/run_script.sh", run_script),
                           ("/workspace/parser.py", parser_py),
                           ("/workspace/restore_fixtures.py", fixture_script),
                           ("/workspace/entryscript.sh", entryscript)):
            await sbx.write_file(path, data)
        run = await sbx.run_command("bash /workspace/entryscript.sh",
                                    timeout=_GRADE_TIMEOUT_SECONDS)
        # Keep the grader's own evidence beside the counts. Without it a 0/N result cannot
        # be told apart from a patch that never applied, a build that never compiled, and a
        # test selector that matched nothing — three different failures that look identical
        # from the counts alone. Setup is fail-fast; test failures still reach the parser.
        evidence = await _save_grader_evidence(sbx, workspace_dir, run)
        try:
            results = json.loads(evidence.get("output.json", ""))
        except (ValueError, TypeError):
            report = _grade_report({"tests": []}, row, evidence, profile=profile)
            if report.get("error_code") in {"no_tests_executed", "test_results_missing", None}:
                report.update(error_code="no_output_json", error_details="the test run produced no valid output.json", resolved=False)
            return report
        return _grade_report(results, row, evidence, profile=profile)
    except Exception as e:  # noqa: BLE001
        return {"error_code": "grade_failed", "error_details": str(e)}
    finally:
        if sbx is not None:
            with contextlib.suppress(Exception):
                await sandbox_manager.release("docker", reuse_key=reuse)


def _grader_fingerprint(grader_repo: str) -> str:
    """Bind resume to the actual local grader assets, including uncommitted changes."""
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(grader_repo):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
        for name in sorted(files):
            if name != "Dockerfile" and not name.endswith((".py", ".sh")):
                continue
            path = path_manager.resolve_under(directory, name)
            digest.update(os.path.relpath(path, grader_repo).encode() + b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _counts_from_report(report_iid: dict, n_f2p: int, n_p2p: int) -> dict:
    """Summarize the official report for benchmark results.

    ``tests_status`` maps FAIL_TO_PASS / PASS_TO_PASS each to {success:[...], failure:[...]};
    we surface only how many of each passed, plus swebench's own ``resolved`` verdict.
    """
    ts = report_iid.get("tests_status") or {}
    f2p_ok = len((ts.get("FAIL_TO_PASS") or {}).get("success") or [])
    p2p_ok = len((ts.get("PASS_TO_PASS") or {}).get("success") or [])
    return {
        "fail_to_pass_resolved": f2p_ok, "fail_to_pass_total": n_f2p,
        "pass_to_pass_preserved": p2p_ok, "pass_to_pass_total": n_p2p,
        "resolved": bool(report_iid.get("resolved")),
    }


async def _grade_verified(workspace_dir: str, row: dict, *, patch: str) -> dict:
    """Grade a frozen patch in an isolated sandbox with the official SWE-bench harness."""
    from swebench.harness.grading import get_eval_report
    from swebench.harness.utils import make_test_spec

    from agentevolver.sandbox import sandbox_manager

    instance_id = row["instance_id"]
    patch = strip_binary_hunks(patch)
    if not patch.strip():
        return {"resolved": False, "grader_note": "empty submission"}
    try:
        spec = make_test_spec(row)
    except Exception as e:  # noqa: BLE001
        return {"error_code": "spec_failed", "error_details": str(e)}

    # Apply the agent's patch to the image-native /testbed (editable install lives there), then
    # run swebench's eval_script; capture everything to a log the parser reads.
    namespace = hashlib.sha256(os.path.realpath(workspace_dir).encode()).hexdigest()[:16]
    reuse = f"eval-{namespace}-{instance_id}"
    sbx = None
    try:
        # A separate, throwaway container from the SAME image the agent used. Root + FULLY
        # OPEN network so the eval setup (pip install -e .[test]) can run; fully-open needs no
        # egress relay. Grader isolation does not weaken the agent's — its sandbox stays offline.
        sbx = await sandbox_manager.acquire(
            "docker", reuse_key=reuse, image=row["image"], network=True,
            allow_hosts=[], deny_hosts=[], user="0:0", workdir="/testbed")
        await sbx.write_file("/tmp/agent_patch.diff", patch)
        await sbx.write_file("/tmp/eval_script.sh", spec.eval_script)
        applied = await sbx.run_command(
            "cd /testbed && git config --global --add safe.directory /testbed && "
            "git apply -v /tmp/agent_patch.diff", timeout=120)
        if not applied.success or applied.exit_code != 0:
            await _save_grader_evidence(sbx, workspace_dir, applied)
            return {"error_code": "patch_apply_failed", "error_details": applied.stderr or applied.error}
        execution = await sbx.run_command(
            "bash /tmp/eval_script.sh > /tmp/test_output.log 2>&1", timeout=_GRADE_TIMEOUT_SECONDS)
        await _save_grader_evidence(sbx, workspace_dir, execution)
        if not execution.success:
            return {"error_code": "grader_execution_failed", "error_details": execution.error}
        try:
            log_text = await sbx.read_file("/tmp/test_output.log")
        except Exception as e:  # noqa: BLE001
            return {"error_code": "no_test_output", "error_details": f"eval produced no log ({e})"}
    except Exception as e:  # noqa: BLE001
        return {"error_code": "grade_failed", "error_details": str(e)}
    finally:
        if sbx is not None:
            with contextlib.suppress(Exception):
                await sandbox_manager.release("docker", reuse_key=reuse)

    # Parse the captured log with swebench's per-repo parser, on the host (oracle-side).
    evidence_dir = path_manager.under(os.path.dirname(workspace_dir), P.PROJECT_EVALUATION)
    os.makedirs(evidence_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".log", dir=evidence_dir, delete=False) as fh:
        fh.write(log_text)
        log_path = fh.name
    try:
        prediction = {"instance_id": instance_id, "model_name_or_path": "agentevolver",
                      "model_patch": patch}
        report = get_eval_report(spec, prediction, log_path, include_tests_status=True)
        if not report.get(instance_id, {}).get("tests_status"):
            return {"error_code": "test_results_missing", "error_details": "no parsed test results", "log_path": log_path}
        return _counts_from_report(report.get(instance_id, {}),
                                   len(spec.FAIL_TO_PASS), len(spec.PASS_TO_PASS))
    except Exception as e:  # noqa: BLE001
        return {"error_code": "report_failed", "error_details": str(e)}
