"""TaskManager — priority queue scheduler for user tasks and self-evolution tasks.

Design:
  - Two logical queues share one asyncio.PriorityQueue:
      * User tasks   (TaskCategory.USER)   — higher scheduling weight
      * Evolver tasks (TaskCategory.EVOLVER) — background, filled around user work
  - Per-entity throttle: at most one EVOLVER task per entity_key in flight,
    preventing optimisation storms.
  - Full async lifecycle: submit → running → done/failed/cancelled.
  - JSON persistence: tasks survive restarts; completed tasks are archived.
  - Worker pool: configurable concurrency, each slot is an asyncio.Task.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.response.types import Response
from agentevolver.task.types import Task, TaskPriority, TaskStatus
from agentevolver.utils import Singleton, file_lock, make_id

# ---------------------------------------------------------------------------
# Extended task metadata
# ---------------------------------------------------------------------------

class TaskCategory(str, Enum):
    """Logical category controlling scheduling weight."""
    USER    = "user"     # Submitted by external caller / meta-agent for user goal
    EVOLVER = "evolver"  # Self-evolution / optimisation / evaluation tasks


class TaskRecord(BaseModel):
    """Persisted envelope around a Task with category and dependency metadata."""

    task: Task
    category: TaskCategory = TaskCategory.USER

    # entity_key is set for EVOLVER tasks to enable per-entity throttle.
    # Format: "<entity_type>/<entity_name>", e.g. "tool/bash_tool"
    entity_key: Optional[str] = Field(default=None)

    # Task IDs that must reach DONE before this task is eligible to run.
    depends_on: List[str] = Field(default_factory=list)

    # Lightweight task-graph execution contract.  Resource declarations are explicit:
    # an empty set preserves legacy scheduling, while declared paths prevent two workers
    # from concurrently reading/writing overlapping state.
    read_set: List[str] = Field(default_factory=list)
    write_set: List[str] = Field(default_factory=list)
    owner: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    token_budget: Optional[int] = None
    acceptance: List[str] = Field(default_factory=list)

    # Set only while loading a task that was RUNNING when the process stopped. The
    # handler must consult Trace/ExecutionCheckpoint before it may execute it again.
    recovered_from_running: bool = False

    # Result / error surfaced after completion.
    result: Optional[Any] = Field(default=None)
    error: Optional[str] = Field(default=None)

    # Retry bookkeeping
    max_retries: int = Field(default=0)
    retry_count: int = Field(default=0)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def effective_priority(self) -> int:
        """Compute scheduling int — lower = higher priority in PriorityQueue."""
        # USER tasks get a 1000-point boost over EVOLVER tasks
        base = -self.task.priority.value  # negate so higher enum = more negative = pops first
        category_penalty = 0 if self.category == TaskCategory.USER else 1000
        return base + category_penalty


# ---------------------------------------------------------------------------
# Callable type for task handlers
# ---------------------------------------------------------------------------

TaskHandler = Callable[[TaskRecord], Coroutine[Any, Any, Any]]


class TaskDeferred(RuntimeError):
    """A handler cannot safely continue until an external decision is recorded."""

    def __init__(self, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager(metaclass=Singleton):
    """Singleton task scheduler with two-tier queuing and per-entity throttle.

    Usage::

        handler = async def run(record): ...

        await task_manager.initialize(log_root="output/tasks/log", handler=handler)
        await task_manager.start(num_workers=4)

        task_id = await task_manager.submit(
            content="Refactor bash_tool",
            category=TaskCategory.EVOLVER,
            entity_key="tool/bash_tool",
        )

        record = await task_manager.get(task_id)
        await task_manager.stop()
    """

    def __init__(self) -> None:
        # (effective_priority, submission_order, task_id) → used for stable ordering
        self._queue: asyncio.PriorityQueue[Tuple[int, int, str]] = asyncio.PriorityQueue()
        self._records: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

        # entity_key → task_id of the currently running evolver task for that entity
        self._running_evolver: Dict[str, str] = {}

        self._workers: List[asyncio.Task] = []
        self._handler: Optional[TaskHandler] = None
        self._log_root: Optional[str] = None
        self._persist_path: Optional[str] = None
        self._archive_path: Optional[str] = None
        # Record identity → (active file, archive file). A Gateway process serves many
        # session log roots at once; binding the next session must not move an already
        # queued task's durable home or write every session's tasks into one file.
        self._record_paths: Dict[int, Tuple[str, str]] = {}

        self._submission_counter: int = 0
        self._running: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(
        self,
        log_root: str,
        handler: Optional[TaskHandler] = None,
    ) -> None:
        """Set log_root, attach handler, and load persisted pending tasks."""
        from agentevolver.paths import path_manager as _path_manager
        _path_manager.on_rebind(self._follow_session)
        self._log_root = log_root
        self._handler = handler
        # Directory is created on first save, so a host that binds a session later
        # (the Gateway) leaves no empty tag-level task directory behind.
        self._persist_path = str(path_manager.under(log_root, P.LOG_TASKS))
        self._archive_path = str(path_manager.under(log_root, P.LOG_TASKS_ARCHIVE))

        await self._load()

        logger.info(
            f"| 📋 TaskManager initialised — {len(self._records)} task(s) loaded "
            f"(log_root={log_root})"
        )

    def _follow_session(self) -> None:
        """Re-point at the newly bound session's log root.

        Subscribed to `path_manager` rather than called by the gateway. Six managers used
        to be re-pointed by name on every session change, which meant six copies of "the
        current log root" kept in step by remembering to add a line — and the forgotten
        line writes this session's files into the previous session's directory without
        erroring.
        """
        from agentevolver.paths import path_manager

        roots = path_manager.session_roots()
        if roots:
            self.rebind(str(roots["log"]))

    def rebind(self, log_root: str) -> None:
        """Re-point task persistence at a newly bound session's task directory.

        Long-lived hosts (the Gateway) initialize the queue once, before any session
        exists; binding a session moves ``tasks.json`` under that session's log root.
        """
        if log_root == self._log_root:
            return
        self._log_root = log_root
        self._persist_path = str(path_manager.under(log_root, P.LOG_TASKS))
        self._archive_path = str(path_manager.under(log_root, P.LOG_TASKS_ARCHIVE))

    async def start(self, num_workers: int = 4) -> None:
        """Start the worker pool.  Safe to call multiple times."""
        if self._running:
            return
        self._running = True
        for i in range(num_workers):
            w = asyncio.create_task(
                self._worker(i), name=f"task-worker-{i}"
            )
            self._workers.append(w)
        logger.info(f"| ▶️  TaskManager started ({num_workers} workers)")

    async def stop(self) -> None:
        """Drain the queue, cancel workers, and persist state."""
        if not self._running:
            return
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        await self._save()
        logger.info("| ⏹️  TaskManager stopped")

    def set_handler(self, handler: TaskHandler) -> None:
        """Attach or replace the task execution handler at runtime."""
        self._handler = handler

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        content: str,
        category: TaskCategory = TaskCategory.USER,
        priority: TaskPriority = TaskPriority.NORMAL,
        entity_key: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        read_set: Optional[List[str]] = None,
        write_set: Optional[List[str]] = None,
        owner: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        token_budget: Optional[int] = None,
        acceptance: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: int = 0,
        session_id: Optional[str] = None,
    ) -> str:
        """Submit a task and return its task_id.

        For EVOLVER tasks, if an evolver task for the same entity_key is
        already queued (PENDING) or running, the new task is silently
        deduplicated and the existing task_id is returned.
        """
        async with self._lock:
            # Throttle: one evolver task per entity_key
            if category == TaskCategory.EVOLVER and entity_key:
                existing_id = self._find_active_evolver(entity_key)
                if existing_id:
                    logger.debug(
                        f"| 🔄 Evolver task for '{entity_key}' already active "
                        f"({existing_id}) — skipping duplicate"
                    )
                    return existing_id

            task = Task(
                id=make_id(),
                content=content,
                files=files or [],
                priority=priority,
                status=TaskStatus.PENDING,
                session_id=session_id,
                metadata=metadata or {},
            )
            record = TaskRecord(
                task=task,
                category=category,
                entity_key=entity_key,
                depends_on=depends_on or [],
                read_set=read_set or [],
                write_set=write_set or [],
                owner=owner,
                model=model,
                reasoning_effort=reasoning_effort,
                token_budget=token_budget,
                acceptance=acceptance or [],
                max_retries=max_retries,
            )
            self._records[task.id] = record
            if self._persist_path and self._archive_path:
                self._record_paths[id(record)] = (
                    self._persist_path, self._archive_path,
                )

            self._submission_counter += 1
            await self._queue.put(
                (record.effective_priority(), self._submission_counter, task.id)
            )
            await self._save_unlocked()

        logger.info(
            f"| ➕ Task submitted: {task.id} "
            f"[{category.value}/{priority.name}] {content}"
        )
        return task.id

    async def get(self, task_id: str) -> Optional[TaskRecord]:
        """Return the TaskRecord for task_id, or None."""
        async with self._lock:
            return self._records.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or confirmation-waiting task. Returns True if cancelled."""
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record.task.status not in (
                TaskStatus.PENDING, TaskStatus.WAITING_CONFIRMATION,
            ):
                return False
            record.task.mark_cancelled()
            record.finished_at = datetime.now(timezone.utc)
            await self._save_unlocked()
        logger.info(f"| ❌ Task cancelled: {task_id}")
        return True

    async def resume_waiting(self, task_id: str) -> bool:
        """Requeue a task after its durable reconciliation facts were recorded."""
        async with self._lock:
            record = self._records.get(task_id)
            if (
                record is None
                or record.task.status is not TaskStatus.WAITING_CONFIRMATION
            ):
                return False
            record.task.status = TaskStatus.PENDING
            record.task.updated_at = datetime.now(timezone.utc)
            record.error = None
            self._submission_counter += 1
            await self._queue.put(
                (record.effective_priority(), self._submission_counter, task_id)
            )
            await self._save_unlocked()
        logger.info(f"| ▶️ Task resumed after reconciliation: {task_id}")
        return True

    async def list(
        self,
        status: Optional[TaskStatus] = None,
        category: Optional[TaskCategory] = None,
    ) -> List[TaskRecord]:
        """Return all tasks, optionally filtered by status and/or category."""
        async with self._lock:
            records = list(self._records.values())
        if status:
            records = [r for r in records if r.task.status == status]
        if category:
            records = [r for r in records if r.category == category]
        records.sort(key=lambda r: r.created_at)
        return records

    async def stats(self) -> Dict[str, Any]:
        """Return a summary of task counts by status and category."""
        async with self._lock:
            records = list(self._records.values())

        counts: Dict[str, int] = {}
        for r in records:
            key = f"{r.category.value}/{r.task.status.value}"
            counts[key] = counts.get(key, 0) + 1

        return {
            "total": len(records),
            "queue_size": self._queue.qsize(),
            "running_evolver_entities": list(self._running_evolver.keys()),
            "counts": counts,
        }

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        logger.debug(f"| 🔧 Task worker-{worker_id} started")
        while self._running:
            try:
                try:
                    priority, order, task_id = await asyncio.wait_for(
                        self._queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    await self._execute(task_id)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"| ❌ Worker-{worker_id} unexpected error: {e}", exc_info=True)

        logger.debug(f"| 🔧 Task worker-{worker_id} stopped")

    async def _execute(self, task_id: str) -> None:
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return

            # Skip cancelled tasks
            if record.task.status == TaskStatus.CANCELLED:
                return

            # Check dependencies
            if not await self._deps_satisfied_unlocked(record):
                # Re-enqueue at lower priority to check again later
                self._submission_counter += 1
                await self._queue.put(
                    (record.effective_priority() + 500, self._submission_counter, task_id)
                )
                return

            conflict = self._resource_conflict_unlocked(task_id, record)
            if conflict is not None:
                self._submission_counter += 1
                await self._queue.put(
                    (record.effective_priority() + 300, self._submission_counter, task_id)
                )
                logger.debug(
                    f"| ⏸️ Task {task_id} waits for resource owner {conflict}"
                )
                return

            # Evolver throttle (runtime check after deps)
            if record.category == TaskCategory.EVOLVER and record.entity_key:
                if record.entity_key in self._running_evolver:
                    # Already another running for this entity — re-enqueue
                    self._submission_counter += 1
                    await self._queue.put(
                        (record.effective_priority() + 200, self._submission_counter, task_id)
                    )
                    return
                self._running_evolver[record.entity_key] = task_id

            record.task.mark_running()
            record.started_at = datetime.now(timezone.utc)
            await self._save_unlocked()

        logger.info(f"| ▶️  Running task: {task_id} — {record.task.content}")

        handler = self._handler
        try:
            if handler is None:
                raise RuntimeError("No handler registered on TaskManager")
            result = await handler(record)
            # A handler that returns rather than raises is not automatically a
            # success. An agent run that was force-stopped — the no-progress guard
            # firing, a constraint tripping — returns a Response with
            # success=False, and marking that DONE reports work that did not
            # happen. Seen on ProgramBench: a run stopped at step 8 with no source
            # file written was recorded as `finished as done`, and its empty
            # submission went on to be scored like a real attempt. Only an explicit
            # success=False downgrades; handlers that return None or a plain value
            # keep the previous behaviour.
            failed = isinstance(result, Response) and result.success is False
            async with self._lock:
                if failed:
                    record.task.mark_failed()
                    record.error = result.message or "handler reported failure"
                else:
                    record.task.mark_done()
                record.result = result
                record.finished_at = datetime.now(timezone.utc)
                self._clear_evolver_slot(record)
                await self._save_unlocked()
            if failed:
                logger.warning(f"| ❌ Task failed: {task_id} — {record.error}")
            else:
                logger.info(f"| ✅ Task done: {task_id}")

        except asyncio.CancelledError:
            async with self._lock:
                record.task.mark_cancelled()
                record.finished_at = datetime.now(timezone.utc)
                self._clear_evolver_slot(record)
                await self._save_unlocked()
            logger.info(f"| ⏹️  Task cancelled while running: {task_id}")

        except TaskDeferred as deferred:
            async with self._lock:
                record.task.mark_waiting_confirmation()
                record.error = deferred.reason
                record.result = deferred.details
                record.finished_at = None
                self._clear_evolver_slot(record)
                await self._save_unlocked()
            logger.warning(
                f"| ⏸️ Task waiting for effect reconciliation: {task_id} — "
                f"{deferred.reason}"
            )

        except Exception as e:
            async with self._lock:
                if record.retry_count < record.max_retries:
                    record.retry_count += 1
                    record.task.status = TaskStatus.PENDING
                    record.task.updated_at = datetime.now(timezone.utc)
                    self._submission_counter += 1
                    await self._queue.put(
                        (record.effective_priority(), self._submission_counter, task_id)
                    )
                    logger.warning(
                        f"| 🔁 Task {task_id} failed — retry "
                        f"{record.retry_count}/{record.max_retries}: {e}"
                    )
                else:
                    record.task.mark_failed()
                    record.error = str(e)
                    record.finished_at = datetime.now(timezone.utc)
                    self._clear_evolver_slot(record)
                    logger.error(f"| ❌ Task failed: {task_id} — {e}", exc_info=True)
                await self._save_unlocked()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_active_evolver(self, entity_key: str) -> Optional[str]:
        """Return task_id if a PENDING or RUNNING evolver task exists for entity_key."""
        for tid, rec in self._records.items():
            if (
                rec.entity_key == entity_key
                and rec.category == TaskCategory.EVOLVER
                and rec.task.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            ):
                return tid
        return None

    def _clear_evolver_slot(self, record: TaskRecord) -> None:
        if record.category == TaskCategory.EVOLVER and record.entity_key:
            self._running_evolver.pop(record.entity_key, None)

    async def _deps_satisfied_unlocked(self, record: TaskRecord) -> bool:
        for dep_id in record.depends_on:
            dep = self._records.get(dep_id)
            if dep is None or dep.task.status != TaskStatus.DONE:
                return False
        return True

    @staticmethod
    def _resource_overlap(left: str, right: str) -> bool:
        """Whether two normalized resource names denote the same path/subtree."""
        left_parts = tuple(part for part in str(left).replace("\\", "/").split("/") if part not in ("", "."))
        right_parts = tuple(part for part in str(right).replace("\\", "/").split("/") if part not in ("", "."))
        if not left_parts or not right_parts:
            return left_parts == right_parts
        common = min(len(left_parts), len(right_parts))
        return left_parts[:common] == right_parts[:common]

    def _resource_conflict_unlocked(
        self, task_id: str, candidate: TaskRecord,
    ) -> Optional[str]:
        """Return the running task whose declared read/write set conflicts."""
        for other_id, other in self._records.items():
            if other_id == task_id or other.task.status is not TaskStatus.RUNNING:
                continue
            pairs = [
                *[(path, held) for path in candidate.write_set for held in other.write_set],
                *[(path, held) for path in candidate.write_set for held in other.read_set],
                *[(path, held) for path in candidate.read_set for held in other.write_set],
            ]
            if any(self._resource_overlap(left, right) for left, right in pairs):
                return other_id
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _save(self) -> None:
        async with self._lock:
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        """Persist active task records (no lock needed — caller holds _lock)."""
        if not self._persist_path:
            return
        try:
            grouped: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
            for tid, rec in self._records.items():
                paths = self._record_paths.get(id(rec))
                if paths is None:
                    if not self._persist_path or not self._archive_path:
                        continue
                    paths = (self._persist_path, self._archive_path)
                    self._record_paths[id(rec)] = paths
                buckets = grouped.setdefault(paths, {"active": {}, "done": {}})
                payload = json.loads(rec.model_dump_json())
                if rec.task.status in (
                    TaskStatus.PENDING,
                    TaskStatus.WAITING_CONFIRMATION,
                    TaskStatus.RUNNING,
                ):
                    buckets["active"][tid] = payload
                elif rec.task.status in (
                    TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED,
                ):
                    buckets["done"][tid] = payload

            for (persist_path, archive_path), buckets in grouped.items():
                data = {
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "active": buckets["active"],
                }
                os.makedirs(os.path.dirname(persist_path), exist_ok=True)
                async with file_lock(persist_path):
                    with open(persist_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

                # Append completed tasks to their owning session archive.
                done = buckets["done"]
                if not done:
                    continue
                existing: Dict[str, Any] = {}
                if os.path.exists(archive_path):
                    try:
                        with open(archive_path, "r", encoding="utf-8") as f:
                            existing = json.load(f).get("tasks", {})
                    except Exception:
                        pass
                existing.update(done)
                archive_data = {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "tasks": existing,
                }
                async with file_lock(archive_path):
                    with open(archive_path, "w", encoding="utf-8") as f:
                        json.dump(archive_data, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"| ⚠️  TaskManager persist failed: {e}")

    async def _load(self) -> None:
        """Restore PENDING tasks from disk into the in-memory queue."""
        if not self._persist_path or not self._archive_path:
            return
        await self._load_path(self._persist_path, self._archive_path)

    async def restore_from(self, log_root: str) -> int:
        """Merge pending tasks from one session log without rebinding other records."""
        persist_path = str(path_manager.under(log_root, P.LOG_TASKS))
        archive_path = str(path_manager.under(log_root, P.LOG_TASKS_ARCHIVE))
        async with self._lock:
            return await self._load_path(persist_path, archive_path)

    async def _load_path(self, persist_path: str, archive_path: str) -> int:
        if not os.path.exists(persist_path):
            return 0
        try:
            with open(persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            active = data.get("active", {})
            restored = 0
            for tid, raw in active.items():
                try:
                    record = TaskRecord.model_validate(raw)
                    # RUNNING means the process died without a terminal record. Queue it
                    # for the handler, but mark it so ExecutionCheckpoint must authorize
                    # continuation before any provider/tool call is made.
                    if record.task.status == TaskStatus.RUNNING:
                        record.task.status = TaskStatus.PENDING
                        record.task.updated_at = datetime.now(timezone.utc)
                        record.started_at = None
                        record.recovered_from_running = True
                    if record.task.status in (
                        TaskStatus.PENDING, TaskStatus.WAITING_CONFIRMATION,
                    ):
                        if tid in self._records:
                            logger.warning(
                                f"| ⚠️ Duplicate persisted task id {tid}; keeping the first record"
                            )
                            continue
                        self._records[tid] = record
                        self._record_paths[id(record)] = (persist_path, archive_path)
                        if record.task.status is TaskStatus.PENDING:
                            self._submission_counter += 1
                            await self._queue.put(
                                (record.effective_priority(), self._submission_counter, tid)
                            )
                        restored += 1
                except Exception as e:
                    logger.warning(f"| ⚠️  Failed to restore task {tid}: {e}")
            if restored:
                logger.info(f"| 📂 Restored {restored} pending task(s) from disk")
            return restored
        except Exception as e:
            logger.warning(f"| ⚠️  TaskManager load failed: {e}")
            return 0


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

task_manager = TaskManager()
