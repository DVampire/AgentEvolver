import os
import asyncio
import contextlib
import hashlib
import json
import signal
import tempfile
import shutil
import sys
from typing import Optional, List, Dict, Any

from pydantic import Field, ConfigDict, PrivateAttr

from agentevolver.benchmark.types import Benchmark, Task, Stats, EvaluationResult
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
    path: str = Field(default="datasets/ProgramBench-Tests", description="Local directory holding the per-branch test blobs.")
    hf_repo_id: str = Field(default="programbench/ProgramBench-Tests", description="HuggingFace repo to download the test blobs from when missing locally.")

    # Evaluation knobs (passed through to the `programbench eval` CLI)
    docker_cpus: int = Field(default=10, description="CPU cores allotted per docker container during evaluation.")
    branch_workers: int = Field(default=1, description="Number of test branches to run in parallel within an instance.")
    eval_timeout: int = Field(default=3600, description="Per-task timeout (seconds) for the `programbench eval` subprocess.")

    _data_records: List[Dict] = PrivateAttr(default_factory=list)
    _instances: Dict[str, Dict] = PrivateAttr(default_factory=dict)
    _index: int = PrivateAttr(default=0)
    _tasks: List[Task] = PrivateAttr(default_factory=list)

    system_prompt: Optional[str] = Field(default=SYSTEM_PROMPT, description="The system prompt for the benchmark")

    def __init__(self, base_dir: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, **kwargs):
        super().__init__(base_dir=base_dir, start=start, end=end, **kwargs)

    async def initialize(self):
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
        await self.reset()

    async def reset(self) -> Optional[Task]:
        self._index = 0
        self._tasks = []
        return await self.step()

    async def step(self) -> Optional[Task]:
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

    def instances(self) -> List[Dict]:
        return list(self._data_records)

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

    async def eval(self, task: Task) -> Task:
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
                cli, "eval", log_root, "--filter", f"^{re.escape(task.task_id)}$",
                "--branch-workers", str(self.branch_workers),
                "--docker-cpus", str(self.docker_cpus), "--force",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
                raise RuntimeError(f"programbench exited {process.returncode}; evidence: {log_root}")
            with open(eval_json, encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("error_code"):
                report = {"error_code": raw["error_code"], "error_details": raw.get("error_details"),
                          "eval_json": str(eval_json)}
            else:
                from programbench.submission import score_instance, test_results_map
                tests = test_results_map(eval_json, instance)
                if not tests:
                    raise RuntimeError("the grader produced no scored test results")
                score = score_instance(eval_json, instance)
                passed = sum(bool(value) for value in tests.values())
                report = {"resolved": score >= 1.0, "passed_tests": passed,
                          "total_tests": len(tests), "eval_json": str(eval_json)}
        except Exception as error:
            report = {"error_code": "evaluation_failed", "error_details": str(error)}
        task.evaluation = EvaluationResult.from_report(report, score=score)
        task.score = task.evaluation.score
        self._tasks = [previous for previous in self._tasks if previous.task_id != task.task_id]
        self._tasks.append(task)
        return task

    async def stats(self) -> Optional[Stats]:
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
