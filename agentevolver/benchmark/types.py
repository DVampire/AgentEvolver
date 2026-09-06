import asyncio
import contextlib
import hashlib
import json
import os
import inspect
import math
from typing import Dict, Any, Optional, Type, Literal, ClassVar
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr
import inflection

from agentevolver.utils.file_utils import atomic_write_text, file_lock
from agentevolver.logger import logger
from agentevolver.dynamic import dynamic_manager
from agentevolver.config import config
from agentevolver.paths import P, path_manager
from agentevolver.utils import assemble_workspace_path, dedent, is_same


class JudgeResult(BaseModel):
    """Structured output for the LLM-based answer judge."""

    consistent: bool = Field(
        description="True if the predicted answer is consistent with the ground-truth answer."
    )
    reason: str = Field(default="", description="A brief justification for the judgement.")


class EvaluationResult(BaseModel):
    """Host-side final evaluation. An execution error is not an incorrect answer."""

    status: Literal["passed", "failed", "error"]
    score: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_report(cls, report: Dict[str, Any], score: Optional[float] = None):
        if report.get("error_code"):
            return cls(status="error", details=report)
        value = float(bool(report.get("resolved"))) if score is None else score
        return cls(status="passed" if value >= 1.0 else "failed", score=value, details=report)


class Task(BaseModel):
    """Data model for a single benchmark task"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    # Input
    task_id: str = Field(description="Unique identifier for the task")
    input: str = Field(default="", description="The input prompt/question for the task")
    system_prompt: Optional[str] = Field(default=None, description="The system prompt for the task")
    ground_truth: Optional[Any] = Field(default=None, description="The expected correct answer")

    # Output
    reasoning: Optional[str] = Field(default=None, description="The reasoning process")
    result: Optional[Any] = Field(default=None, description="The final answer")
    time: Optional[float] = Field(
        default=0.0, description="The time taken to complete the task in seconds"
    )
    score: Optional[float] = Field(
        default=None, description="Final score; None before grading or on evaluation error"
    )
    evaluation: Optional[EvaluationResult] = None

    extra: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional task-specific metadata"
    )


class Stats(BaseModel):
    """Data model for benchmark statistics"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    accuracy: float = Field(default=0.0, description="Overall accuracy score")
    total: int = Field(default=0, description="Total number of tasks")
    correct: int = Field(default=0, description="Number of correct tasks")
    wrong: int = Field(default=0, description="Number of wrong tasks")
    attempted: int = Field(
        default=0, description="Number of distinct evaluated tasks, including errors"
    )
    scored: int = Field(default=0, description="Number of tasks with a valid score")
    errors: int = Field(default=0, description="Number of evaluation errors")
    mean_score: float = Field(
        default=0.0, description="Mean score over scored tasks, including partial credit"
    )
    times: Dict[str, float] = Field(
        default_factory=dict, description="Time taken for each task (task_id -> seconds)"
    )
    average_time: float = Field(default=0.0, description="Average time per task in seconds")
    extra: Dict[str, Any] = Field(
        default_factory=dict, description="Additional statistics or information"
    )


class BenchmarkTaskContext(BaseModel):
    """Task-local execution data; never exposes a Benchmark or an answer key."""

    task_id: str
    session_dir: str
    workspace_dir: str
    agent_workspace: str
    container_workspace: Optional[str] = None
    user: str = "1000:1000"
    image: Optional[str] = None
    container_name: Optional[str] = None
    resource_key: str
    submission_path: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    submission: Optional[Dict[str, Any]] = None
    resumed: bool = False
    completed: bool = False
    permissions_changed: bool = False
    egress_audit: Dict[str, Any] = Field(default_factory=dict)


class BenchmarkInfo(BaseModel):
    """Detached public metadata, without implementation classes or instances."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    initialized: bool = False
    config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evaluation: Dict[str, Any] = Field(default_factory=dict)


class Benchmark(BaseModel):
    """Uniform async interface; subclasses implement private hooks only.

    initialize loads without consuming a task; reset rewinds and returns the first;
    step returns the next or None. eval always finishes grading and returns a Task
    with EvaluationResult. stats describes the latest evaluation per task ID.
    llm_judge is an explicit scoring utility; cleanup releases resources.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    PUBLIC_METHODS: ClassVar[frozenset[str]] = frozenset(
        {
            "initialize",
            "reset",
            "step",
            "eval",
            "llm_judge",
            "stats",
            "cleanup",
            "info",
            "task_payload",
            "prepare",
            "submit",
        }
    )
    _initialized: bool = PrivateAttr(default=False)
    _initialize_lock: Any = PrivateAttr(default_factory=asyncio.Lock)
    _tasks: list[Task] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)
    _contexts: Dict[str, BenchmarkTaskContext] = PrivateAttr(default_factory=dict)
    _resources: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _task_locks: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _runtime_lock: Any = PrivateAttr(default_factory=asyncio.Lock)
    _shared_paths: set[str] = PrivateAttr(default_factory=set)
    _checked_mounts: set[str] = PrivateAttr(default_factory=set)
    container_workspace: Optional[str] = None
    container_user: str = "1000:1000"
    submission_protocol: str = "answer-v1"
    submission_filename: str = "submission.json"
    resume: bool = False
    history_path: Optional[str] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Pydantic serialization is inherited, not part of the benchmark protocol.
        # Reject additional entry points and overrides of the shared wrappers.
        for name, value in cls.__dict__.items():
            if (
                not name.startswith("_")
                and not hasattr(BaseModel, name)
                and (callable(value) or isinstance(value, (classmethod, staticmethod, property)))
            ):
                raise TypeError(
                    f"{cls.__name__}.{name}: benchmark implementations must use private hooks; "
                    "the public interface is defined by Benchmark"
                )
        for name in (
            "_initialize",
            "_step",
            "_eval",
            "_stats",
            "_reset",
            "_cleanup",
            "_evaluation_info",
            "_prepare",
            "_collect_submission",
            "_cleanup_task",
        ):
            if name in cls.__dict__ and not inspect.iscoroutinefunction(cls.__dict__[name]):
                raise TypeError(f"{cls.__name__}.{name} must be async")

    name: str = Field(default="", description="The name of the benchmark")
    description: str = Field(default="", description="The description of the benchmark")

    # Dataset-related fields
    split: str = Field(default="test", description="Dataset split")
    subset: Optional[str] = Field(default=None, description="Subset name")
    path: str = Field(default="", description="Dataset path")
    base_dir: str = Field(
        default="", description="Base directory for storing benchmark outputs and results"
    )
    start: Optional[int] = Field(
        default=None,
        description="Start index for slicing the dataset (inclusive). None means from the beginning.",
    )
    end: Optional[int] = Field(
        default=None,
        description="End index for slicing the dataset (exclusive). None means to the last item.",
    )

    # Evaluation
    model_name: Optional[str] = Field(
        default=None,
        description="Model for llm_judge and answer-based scoring; does not replace official coding evaluators.",
    )

    def __init__(
        self,
        base_dir: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        **kwargs,
    ):
        """Initialize benchmark system."""
        super().__init__(start=start, end=end, **kwargs)
        # Auto-set name from class name if not provided
        if not self.name:
            self.name = inflection.underscore(self.__class__.__name__)
        # Auto-set description from docstring if not provided
        if not self.description and self.__class__.__doc__:
            self.description = self.__class__.__doc__.strip().split("\n")[0]
        # Set base_dir
        if base_dir is not None:
            self.base_dir = assemble_workspace_path(base_dir)
        else:
            self.base_dir = str(
                path_manager.under(
                    config.log_root,
                    P.LOG_BENCHMARK,
                    benchmark=self.name,
                )
            )

    def _apply_slice(self, records: list) -> list:
        """Slice records according to start/end fields."""
        if self.start is None and self.end is None:
            return records
        return records[self.start : self.end]

    async def initialize(self) -> None:
        """Load once, without advancing the cursor. Safe to call repeatedly."""
        async with self._initialize_lock:
            if not self._initialized:
                await self._initialize()
                self._index = 0
                self._tasks = []
                self._restore_evaluations()
                self._initialized = True

    async def info(self, *, evaluation_options: Optional[Dict[str, Any]] = None) -> BenchmarkInfo:
        """Metadata for the manager, without loading data or starting a grader."""
        return BenchmarkInfo(
            name=self.name,
            description=self.description,
            initialized=self._initialized,
            evaluation={
                "evaluation_protocol": self.submission_protocol,
                **await self._evaluation_info(evaluation_options or {}),
            },
        )

    def task_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Pure projection of solver-visible fields, used through the manager."""
        return self._task_payload(record)

    def _task_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {key: record[key] for key in ("task_id", "input", "system_prompt") if key in record}

    async def _evaluation_info(self, options: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    async def reset(self, *, split: Optional[str] = None, resume: bool = False) -> Optional[Task]:
        """Clear progress and return the first task; a changed split reloads data.

        Call only after outstanding evaluations have finished.
        """
        if self._contexts:
            raise RuntimeError("cleanup active tasks before resetting the benchmark")
        if split is not None and split != self.split:
            await self.cleanup()
            self.split = split
        await self.initialize()
        await self._reset()
        self._index = 0
        if not resume:
            self._tasks = []
        return await self.step()

    async def step(self) -> Optional[Task]:
        """Get the next task, or None at exhaustion; never evaluate implicitly."""
        await self.initialize()
        task = await self._step()
        if task is not None and not isinstance(task, Task):
            raise TypeError("Benchmark._step must return Task or None")
        return task

    async def eval(self, task: Task) -> Task:
        if not isinstance(task, Task):
            raise TypeError("Benchmark.eval requires a Task")
        async with self._task_locks.setdefault(task.task_id, asyncio.Lock()):
            return await self._evaluate_task(task)

    async def _evaluate_task(self, task: Task) -> Task:
        """Complete one evaluation and record a snapshot, replacing the same ID.

        Cancellation propagates. Grader failures are unscored errors, never implicit
        wrong answers. Official implementations retain their own scoring rules.
        """
        if not isinstance(task, Task):
            raise TypeError("Benchmark.eval requires a Task")
        task_id = task.task_id
        task.score = None
        task.evaluation = None
        try:
            await self.initialize()
            path = (task.extra or {}).get("submission_path")
            if path:
                with open(path, encoding="utf-8") as handle:
                    submission = json.load(handle)
                self._validate_submission(task, submission)
                if submission["sha256"] != task.extra.get("submission_sha256"):
                    raise ValueError("frozen submission changed after submit")
                self._apply_submission(task, submission)
            result = await self._eval(task)
            if not isinstance(result, Task) or result.task_id != task_id:
                raise TypeError("Benchmark._eval must return the evaluated Task with the same ID")
            task = result
            if task.evaluation is None:
                if task.score is None or not math.isfinite(task.score) or not 0 <= task.score <= 1:
                    raise ValueError("Grader returned no valid score in [0, 1]")
                task.evaluation = EvaluationResult.from_report({}, score=task.score)
            elif task.evaluation.status != "error":
                value = task.evaluation.score
                if value is None or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Grader returned an invalid evaluation score")
                if (task.evaluation.status == "passed") != (value >= 1):
                    raise ValueError("Grader returned an inconsistent status and score")
            else:
                task.evaluation.score = None
            task.score = task.evaluation.score
        except Exception as error:
            task.evaluation = EvaluationResult.from_report(
                {
                    "error_code": "evaluation_failed",
                    "error_details": str(error),
                }
            )
            task.score = None
        self._commit_evaluation(task)
        return task

    async def llm_judge(self, task: Task) -> float:
        """Score a task with an LLM judge.

        Uses `self.model_name` to ask the model whether the predicted answer is
        consistent with the ground-truth answer. Returns 1.0 if consistent, else 0.0.
        Falls back to exact matching if the model call fails.
        """
        from agentevolver.model import model_manager
        from agentevolver.message.types import SystemMessage, HumanMessage

        pred = str(task.result).strip() if task.result is not None else ""
        gt = str(task.ground_truth).strip() if task.ground_truth is not None else ""

        if not pred:
            return 0.0
        if not self.model_name:
            return 1.0 if is_same(pred, gt) else 0.0

        system_prompt = dedent("""
            You are a strict grader. You are given a question, a ground-truth answer,
            and a predicted answer. Decide whether the predicted answer is consistent
            with the ground-truth answer (i.e. they convey the same answer to the
            question). Ignore differences in wording, formatting, or extra explanation.
            Set "consistent" to true only if the predicted answer matches the
            ground-truth answer; otherwise set it to false.
        """)
        user_prompt = dedent(f"""
            [Question]
            {task.input}

            [Ground-truth answer]
            {gt}

            [Predicted answer]
            {pred}
        """)

        try:
            response = await model_manager(
                name=self.model_name,
                input={
                    "messages": [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ],
                    "response_format": JudgeResult,
                },
            )
            if response.success and response.parsed_model is not None:
                return 1.0 if response.parsed_model.consistent else 0.0
            logger.warning(
                f"[{self.name}] LLM judge failed for task {task.task_id}: {response.message}"
            )
        except Exception as e:
            logger.warning(f"[{self.name}] LLM judge error for task {task.task_id}: {e}")

        # Fallback to exact match
        return 1.0 if pred and is_same(pred, gt) else 0.0

    async def stats(self) -> Stats:
        """Return consistent counts; benchmark-specific breakdowns live in extra.

        accuracy is fully correct / scored; mean_score includes partial credit.
        Errors are reported separately and excluded from both denominators.
        """
        await self.initialize()
        result = await self._stats()
        if not isinstance(result, Stats):
            raise TypeError("Benchmark._stats must return Stats")
        scored = [task for task in self._tasks if task.score is not None]
        result.attempted = len(self._tasks)
        result.scored = len(scored)
        result.errors = result.attempted - result.scored
        result.correct = sum(task.score >= 1 for task in scored)
        result.wrong = result.scored - result.correct
        result.accuracy = result.correct / result.scored if result.scored else 0.0
        result.mean_score = (
            sum(task.score for task in scored) / result.scored if result.scored else 0.0
        )
        result.times = {task.task_id: task.time for task in self._tasks if task.time is not None}
        result.average_time = (
            sum(result.times.values()) / len(result.times) if result.times else 0.0
        )
        result.extra["evaluation_errors"] = result.errors
        result.extra["completed_task_ids"] = [task.task_id for task in scored]
        result.extra["evaluations"] = {task.task_id: task.model_dump() for task in self._tasks}
        spends = [
            (
                task.result.get("agent_result", {}).get("spend", {})
                if isinstance(task.result, dict)
                else {}
            )
            or (task.extra or {}).get("spend", {})
            for task in self._tasks
        ]
        result.extra["usage"] = {
            key: sum(item.get(key, 0) or 0 for item in spends)
            for key in (
                "n_llm_calls",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        }
        costs = [
            item["total_cost_usd"] for item in spends if item.get("total_cost_usd") is not None
        ]
        result.extra["usage"]["total_cost_usd"] = sum(costs) if costs else None
        result.extra["usage"]["cost_is_estimated"] = True

        return result

    async def cleanup(self, task: Optional[Task] = None, *, reclaim: bool = False) -> None:
        """Release one task or all resources; keep submissions and evaluation evidence."""
        ids = [task.task_id] if task is not None else list(self._contexts)
        errors = []
        for task_id in ids:
            ctx = self._contexts.get(task_id)
            if ctx is None:
                continue
            try:
                await self._cleanup_task(ctx, reclaim=reclaim)
                self._contexts.pop(task_id, None)
            except Exception as error:
                errors.append(error)
        if task is None and self._resources:
            errors.append(RuntimeError("task resources still active; shared permissions retained"))
        elif task is None:
            for path in list(self._shared_paths):
                try:
                    await self._restore_workspace_owner(path)
                    self._shared_paths.remove(path)
                except Exception as error:
                    errors.append(error)
            await self._cleanup()
            self._initialized = False
        if errors:
            raise RuntimeError("benchmark cleanup failed: " + "; ".join(map(str, errors)))

    async def prepare(
        self, task: Task, *, context: Optional[Dict[str, Any]] = None
    ) -> BenchmarkTaskContext:
        """Prepare one selected task. Enumeration never allocates a task sandbox."""
        await self.initialize()
        options = dict(context or {})
        async with self._task_locks.setdefault(task.task_id, asyncio.Lock()):
            if task.task_id in self._contexts:
                return self._contexts[task.task_id].model_copy(deep=True)
            folder = os.path.abspath(
                options.get("session_dir")
                or os.path.join(
                    self.base_dir, "tasks", hashlib.sha256(task.task_id.encode()).hexdigest()[:24]
                )
            )
            workspace = os.path.abspath(
                options.get("workspace_dir") or os.path.join(folder, "workspace")
            )
            ctx = BenchmarkTaskContext(
                task_id=task.task_id,
                session_dir=folder,
                workspace_dir=workspace,
                agent_workspace=workspace
                if options.get("agent_on_host", True)
                else self.container_workspace or workspace,
                container_workspace=self.container_workspace,
                user=self.container_user,
                submission_path=os.path.join(folder, self.submission_filename),
                resource_key=f"benchmark-{self.name}-"
                + hashlib.sha256(folder.encode()).hexdigest()[:20],
                payload={
                    "task": task.input,
                    "system_prompt": task.system_prompt,
                    **self._task_payload(
                        {
                            **(task.extra or {}),
                            "task_id": task.task_id,
                            "input": task.input,
                            "system_prompt": task.system_prompt,
                        }
                    ),
                },
            )
            # Keep evidence/logs outside the deliverable. Do not mutate process-global paths.
            if self.container_workspace:
                for output in (folder, options.get("output_dir")):
                    if (
                        output
                        and os.path.commonpath([workspace, os.path.abspath(output)]) == workspace
                    ):
                        raise ValueError("benchmark logs/output must be outside the task workspace")
            os.makedirs(folder, exist_ok=True)
            self._contexts[task.task_id] = ctx
            try:
                ctx.completed = any(
                    t.task_id == task.task_id and t.evaluation and t.evaluation.status != "error"
                    for t in self._tasks
                )
                if ctx.completed:
                    return ctx.model_copy(deep=True)
                async with file_lock(ctx.submission_path + ".transaction"):
                    if os.path.isfile(ctx.submission_path):
                        if not (self.resume or options.get("resume")):
                            raise ValueError(
                                "existing frozen submission; use resume or a new run directory"
                            )
                        with open(ctx.submission_path, encoding="utf-8") as handle:
                            ctx.submission = json.load(handle)
                        self._validate_submission(task, ctx.submission)
                    receipt = ctx.submission_path + ".receipt.json"
                    legacy_receipt = os.path.join(folder, "submission_receipt.json")
                    for path in (receipt, legacy_receipt):
                        if os.path.isfile(path):
                            with open(path, encoding="utf-8") as handle:
                                digest = json.load(handle).get("sha256")
                            if ctx.submission is None or digest != ctx.submission.get("sha256"):
                                raise ValueError(
                                    "frozen submission is missing or does not match its receipt"
                                )
                    previous = options.get("previous") or {}
                    if ctx.submission is None and (
                        previous.get("final_grade") is not None or previous.get("submission_sha256")
                    ):
                        raise ValueError(
                            "frozen submission is missing; refusing to restart an already submitted Agent"
                        )
                if not ctx.completed and ctx.submission is None:
                    await self._prepare(task, ctx, options)
                return ctx.model_copy(deep=True)
            except BaseException as error:
                if isinstance(error, Exception):
                    self._record_failure(task, "prepare", error)
                await self._cleanup_task(ctx, reclaim=False)
                self._contexts.pop(task.task_id, None)
                raise

    async def submit(self, task: Task, *, output: Any = None) -> Task:
        try:
            return await self._submit_task(task, output=output)
        except Exception as error:
            self._record_failure(task, "submit", error)
            raise

    async def _submit_task(self, task: Task, *, output: Any = None) -> Task:
        """Stop writable task resources and freeze once, before any hidden grading."""
        if task.task_id not in self._contexts:
            await self.prepare(task)
        ctx = self._contexts[task.task_id]
        async with self._task_locks.setdefault(task.task_id, asyncio.Lock()):
            async with file_lock(ctx.submission_path + ".transaction"):
                await self._stop_task_resources(ctx)
                if os.path.isfile(ctx.submission_path):
                    with open(ctx.submission_path, encoding="utf-8") as handle:
                        submission = json.load(handle)
                    self._validate_submission(task, submission)
                else:
                    if os.path.exists(ctx.submission_path + ".receipt.json"):
                        raise ValueError(
                            "frozen submission is missing; refusing to collect new work"
                        )
                    submission = await self._collect_submission(
                        task, ctx, output if output is not None else task.result
                    )
                    self._validate_submission(task, submission)
                    atomic_write_text(
                        ctx.submission_path, json.dumps(submission, ensure_ascii=False, default=str)
                    )
                atomic_write_text(
                    ctx.submission_path + ".receipt.json",
                    json.dumps({"sha256": submission["sha256"]}),
                )
                ctx.submission = submission
                self._apply_submission(task, submission)
                task.extra = {
                    **(task.extra or {}),
                    "submission_path": ctx.submission_path,
                    "submission_sha256": submission["sha256"],
                    "workspace_dir": ctx.workspace_dir,
                }
                return task

    async def _prepare(
        self, task: Task, ctx: BenchmarkTaskContext, options: Dict[str, Any]
    ) -> None:
        """Answer benchmarks need no execution environment."""

    async def _collect_submission(
        self, task: Task, ctx: BenchmarkTaskContext, output: Any
    ) -> Dict[str, Any]:
        body = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "instance_id": task.task_id,
            "protocol": self.submission_protocol,
            "answer": json.loads(body),
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        }

    def _validate_submission(self, task: Task, submission: Dict[str, Any]) -> None:
        body = json.dumps(submission.get("answer"), ensure_ascii=False, sort_keys=True, default=str)
        if (
            submission.get("instance_id") != task.task_id
            or submission.get("protocol") != self.submission_protocol
            or submission.get("sha256") != hashlib.sha256(body.encode()).hexdigest()
        ):
            raise ValueError("invalid frozen submission identity or digest")

    def _apply_submission(self, task: Task, submission: Dict[str, Any]) -> None:
        task.result = submission["answer"]

    async def _stop_task_resources(self, ctx: BenchmarkTaskContext) -> None:
        handle = self._resources.get(ctx.task_id)
        if handle is not None:
            from agentevolver.sandbox import sandbox_manager

            ctx.egress_audit = sandbox_manager.egress_audit(handle) or {}
            await sandbox_manager.release(
                "docker",
                reuse_key=ctx.resource_key,
                resource_id=getattr(handle, "container_name", None),
            )
            self._resources.pop(ctx.task_id, None)
        if ctx.permissions_changed:
            await self._restore_workspace_owner(ctx.session_dir)
            ctx.permissions_changed = False

    async def _cleanup_task(self, ctx: BenchmarkTaskContext, *, reclaim: bool = False) -> None:
        await self._stop_task_resources(ctx)

    async def _acquire_task_sandbox(
        self, ctx: BenchmarkTaskContext, options: Dict[str, Any]
    ) -> None:
        from agentevolver.sandbox import sandbox_manager

        handle = await sandbox_manager.acquire(
            "docker",
            reuse_key=ctx.resource_key,
            start=False,
            image=ctx.image,
            network=False,
            user=ctx.user,
            workdir=ctx.container_workspace,
            mounts={**options.get("mounts", {}), ctx.workspace_dir: ctx.container_workspace},
            env={**options.get("env", {}), "AGENTEVOLVER_TASK_WORKSPACE": ctx.container_workspace},
            timeout_minutes=max(1, int(options.get("timeout", 7200)) // 60),
            **{key: options[key] for key in ("allow_hosts", "deny_hosts") if key in options},
        )
        self._resources[ctx.task_id] = handle
        # Register ownership before starting: cancellation during start must be recoverable.
        await handle.start()
        ctx.container_name = handle.container_name

    async def _blocking(self, function, *args):
        # A cancelled transaction must not leave a thread modifying its artifact.
        pending = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            try:
                await pending
            finally:
                raise

    async def _command(self, argv: list[str], timeout: float = 300):
        """Cancellable setup command, including temporary daemon-owned containers."""
        import signal
        import subprocess
        import uuid

        argv = list(argv)
        owned_container = None
        if argv[:2] == ["docker", "run"] and "--name" not in argv:
            owned_container = "agentevolver-setup-" + uuid.uuid4().hex
            argv[2:2] = ["--name", owned_container]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            except BaseException:
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
        finally:
            if owned_container:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "-f",
                    owned_container,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), 60)
                except BaseException:
                    with contextlib.suppress(ProcessLookupError):
                        cleanup.kill()
                    await cleanup.wait()
                    raise

    async def _grant_workspace(self, path: str, *, protected: tuple[str, ...] = ()) -> None:
        import shlex

        if not os.path.isdir(path):
            return
        script = f"chown -R {shlex.quote(self.container_user)} /target"
        for relative in protected:
            if os.path.isabs(relative) or ".." in relative.split("/"):
                raise ValueError("protected paths must be relative to the task directory")
            target = shlex.quote("/target/" + relative)
            script += f" && if [ -e {target} ]; then chown 0:0 {target} && chmod a-r {target}; fi"
        result = await self._command(
            ["docker", "run", "--rm", "-v", f"{path}:/target", "alpine", "sh", "-c", script]
        )
        if result.returncode:
            raise RuntimeError(f"workspace permission setup failed: {result.stderr[-500:]}")

    async def _prepare_runtime_mounts(self, options: Dict[str, Any]) -> None:
        # This is run-scoped: don't change shared ownership under another task.
        async with self._runtime_lock:
            for path in options.get("writable_paths", []):
                path = os.path.abspath(path)
                if path not in self._shared_paths:
                    await self._grant_workspace(path)
                    self._shared_paths.add(path)
            for source, target in options.get("mounts", {}).items():
                if source in self._checked_mounts:
                    continue
                result = await self._command(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--user",
                        self.container_user,
                        "-v",
                        f"{source}:{target}:ro",
                        "alpine",
                        "sh",
                        "-c",
                        'test -r "$1" && test -x "$1"',
                        "sh",
                        target,
                    ]
                )
                if result.returncode:
                    raise RuntimeError(f"container user cannot read runtime mount: {source}")
                self._checked_mounts.add(source)

    async def _restore_workspace_owner(self, path: str) -> None:
        if os.path.isdir(path):
            result = await self._command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{path}:/target",
                    "alpine",
                    "sh",
                    "-c",
                    f"chown -R {os.getuid()}:{os.getgid()} /target && chmod -R a+rX /target",
                ],
                600,
            )
            if result.returncode:
                raise RuntimeError(f"workspace ownership restore failed: {result.stderr[-500:]}")

    def _restore_evaluations(self) -> None:
        if not self.resume:
            return
        path = os.path.join(self.base_dir, "evaluations.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            self._tasks = [Task.model_validate(value) for value in data.values()]
        if self.history_path and os.path.isfile(self.history_path):
            with open(self.history_path, encoding="utf-8") as handle:
                history = json.load(handle)
            for record in history if isinstance(history, list) else history.get("records", []):
                task = self._historical_task(record)
                if task is not None and not any(t.task_id == task.task_id for t in self._tasks):
                    self._tasks.append(task)

    def _historical_task(self, record: Dict[str, Any]) -> Optional[Task]:
        if record.get("task_id") and record.get("evaluation"):
            return Task.model_validate(record)
        return None

    def _record_failure(self, task: Task, stage: str, error: Exception) -> None:
        task.evaluation = EvaluationResult(
            status="error",
            details={"error_code": f"{stage}_failed", "error_details": str(error), "stage": stage},
        )
        task.score = None
        self._commit_evaluation(task)

    def _commit_evaluation(self, task: Task) -> None:
        try:
            self._record_evaluation(task)
        except Exception as error:
            report = task.evaluation.model_dump() if task.evaluation else None
            task.evaluation = EvaluationResult(
                status="error",
                details={
                    "error_code": "persistence_failed",
                    "error_details": str(error),
                    "grader_result": report,
                },
            )
            task.score = None
        self._tasks = [previous for previous in self._tasks if previous.task_id != task.task_id]
        self._tasks.append(task.model_copy(deep=True))

    def _record_evaluation(self, task: Task) -> None:
        from agentevolver.utils.file_utils import atomic_json_update

        path = os.path.join(self.base_dir, "evaluations.json")
        value = json.loads(json.dumps(task.model_dump(), ensure_ascii=False, default=str))
        atomic_json_update(
            path,
            lambda previous: {**(previous or {}), task.task_id: value},
            default={},
            recover_corrupt=False,
        )

    async def _initialize(self) -> None:
        raise NotImplementedError

    async def _step(self) -> Optional[Task]:
        raise NotImplementedError

    async def _eval(self, task: Task) -> Task:
        raise NotImplementedError

    async def _stats(self) -> Stats:
        raise NotImplementedError

    async def _reset(self) -> None:
        pass

    async def _cleanup(self) -> None:
        pass


class BenchmarkConfig(BaseModel):
    """Benchmark configuration for registration"""

    name: str = Field(description="The name of the benchmark")
    description: str = Field(description="The description of the benchmark")
    version: str = Field(default="1.0.0", description="Version of the benchmark")

    cls: Optional[Type[Benchmark]] = Field(default=None, description="The class of the benchmark")
    instance: Optional[Any] = Field(default=None, description="The instance of the benchmark")
    config: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="The initialization configuration"
    )
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="The metadata")
    code: Optional[str] = Field(default=None, description="Source code")

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "cls": dynamic_manager.get_class_string(self.cls) if self.cls else None,
            "config": self.config,
            "instance": None,  # Don't serialize instance
            "metadata": self.metadata,
            "code": self.code,
        }

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> "BenchmarkConfig":
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description")
        version = data.get("version", "1.0.0")

        cls_ = None
        code = data.get("code")
        if code:
            class_name = dynamic_manager.extract_class_name_from_code(code)
            if class_name:
                try:
                    cls_ = dynamic_manager.load_class(
                        code, class_name=class_name, base_class=Benchmark, context="benchmark"
                    )
                except Exception:
                    cls_ = None

        config = data.get("config", {})
        instance = data.get("instance", None)
        metadata = data.get("metadata", {})

        return cls(
            name=name,
            description=description,
            version=version,
            cls=cls_,
            config=config,
            instance=instance,
            metadata=metadata,
            code=code,
        )
