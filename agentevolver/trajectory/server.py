"""TrajectoryManagerServer — accumulates step-level trajectories from hook events.

Parallels TraceManager/MemoryManagerServer: owns the global ``trajectory_manager``
singleton. A run builds up in memory keyed by ``task_id`` (fed by
``TrajectoryHook`` from the agent lifecycle), is persisted as JSONL on
``finalize``, and can be reward-backfilled afterwards via ``set_reward``
(rewards typically arrive after the agent returns — e.g. from a benchmark judge).

Persistence: ``<log_root>/trajectory/<task_id>.jsonl`` — line 1 is the trajectory
header (metadata), the remaining lines are one serialized step each.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.trajectory.labels import (
    REWARD_LABEL_SCHEMA_VERSION,
    RewardLabel,
    UnsupportedRewardLabel,
)
from agentevolver.trajectory.projector import project_trajectory
from agentevolver.trajectory.types import (
    TRAJECTORY_SCHEMA_VERSION,
    Trajectory,
    TrajectoryContext,
    TrajectoryStep,
)


class TrajectoryManagerServer:
    """Singleton server that builds, persists, and exports agent trajectories."""

    def __init__(self) -> None:
        self.base_dir: Optional[str] = None
        # task_id → trajectory being built / finalized (retained for reward backfill)
        self._trajectories: Dict[str, Trajectory] = {}
        # task_id → the step currently open (lazily created on first observation)
        self._open_steps: Dict[str, TrajectoryStep] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        from agentevolver.config import config
        from agentevolver.utils import assemble_workspace_path

        path_manager.on_rebind(self._follow_session)

        try:
            log_root = config.log_root
        except (AttributeError, KeyError):
            # config not initialized (e.g. isolated tests) — fall back to workspace_root.
            log_root = "workspace_root"
        self.base_dir = assemble_workspace_path(path_manager.under(log_root, P.LOG_MODULE, module="trajectory"))
        logger.info(f"| 📁 Trajectory manager base directory: {self.base_dir}")

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
        """Re-point persistence at ``<log_root>/trajectory`` for a newly bound session.

        Long-lived hosts (the Gateway) initialize this manager once, before any
        session exists; binding a session re-points it so trajectories land under
        that session's own log root.
        """
        from agentevolver.utils import assemble_workspace_path

        self.base_dir = assemble_workspace_path(path_manager.under(log_root, P.LOG_MODULE, module="trajectory"))

    # ------------------------------------------------------------------
    # Build — driven by TrajectoryHook
    # ------------------------------------------------------------------

    def begin(self, ctx: TrajectoryContext) -> None:
        """ON_START — open a fresh trajectory for this run."""
        inp = ctx.input
        self._trajectories[ctx.task_id] = Trajectory(
            session_id=ctx.session_id,
            task_id=ctx.task_id,
            agent_name=ctx.agent_name,
            task_description=inp.get("task") or "",
            metadata={
                k: inp.get(k)
                for k in ("parent_session_id", "subtask_id")
                if inp.get(k)
            },
        )
        self._open_steps.pop(ctx.task_id, None)

    def _current_step(self, task_id: str, step_number: int) -> TrajectoryStep:
        """Return the open step for this task, creating it lazily if needed.

        Lazy creation means we don't depend on a PRE_STEP wiring: the first
        POST_ACTION (or POST_STEP) of a step materializes it.
        """
        step = self._open_steps.get(task_id)
        if step is None or step.step_number != step_number:
            step = TrajectoryStep(step_number=step_number)
            self._open_steps[task_id] = step
        return step

    def add_observation(self, ctx: TrajectoryContext) -> None:
        """POST_ACTION — record one action's result/error into the open step."""
        if ctx.task_id not in self._trajectories:
            return
        inp = ctx.input
        step = self._current_step(ctx.task_id, inp.get("step_number", 0))
        action = inp.get("action") or {}
        step.observations.append({
            "index": action.get("index"),
            "type": action.get("type"),
            "name": action.get("name"),
            "args": action.get("args"),
            "result": _stringify(inp.get("action_result")),
            "error": inp.get("error"),
        })
        execution = inp.get("execution_meta") or {}

    def close_step(self, ctx: TrajectoryContext) -> None:
        """POST_STEP — attach the effective prompt + decision + tokens, then commit."""
        traj = self._trajectories.get(ctx.task_id)
        if traj is None:
            return
        inp = ctx.input
        step = self._current_step(ctx.task_id, inp.get("step_number", 0))
        step.messages_sent = inp.get("messages") or []
        step.reasoning = inp.get("reasoning") or ""
        step.actions = inp.get("plan") or []
        step.token_usage = inp.get("step_tokens") or 0
        step.usage = inp.get("step_usage")
        self._attach_step_trace_lineage(traj, step)
        traj.steps.append(step)
        self._open_steps.pop(ctx.task_id, None)

    def finalize(self, ctx: TrajectoryContext) -> None:
        """ON_STOP — mark outcome and persist. Reward may arrive later."""
        traj = self._trajectories.get(ctx.task_id)
        if traj is None:
            return
        inp = ctx.input
        traj.success = bool(inp.get("success", False))
        traj.final_result = _stringify(inp.get("result"))
        self._attach_run_trace_lineage(traj)
        self._open_steps.pop(ctx.task_id, None)
        self._persist(traj)

    @staticmethod
    def _events_for(traj: Trajectory, step_number: Optional[int] = None):
        """Trace facts that belong to this trajectory, optionally to one step.

        Trajectory is a projection, so provenance must be discovered from trace rather
        than copied through another hook payload. If the in-memory trace was deliberately
        dropped after its retention cap, this returns no evidence and leaves the lineage
        fields absent — an honest unknown instead of a fabricated range.
        """
        from agentevolver.trace.server import trace_manager

        events = [event for event in trace_manager.events(traj.session_id)
                  if event.task_id == traj.task_id]
        if step_number is not None:
            events = [event for event in events if event.step_number == step_number]
        return events

    def _attach_step_trace_lineage(
        self, traj: Trajectory, step: TrajectoryStep,
    ) -> None:
        """Cite the trace range and routed request that produced one training step."""
        from agentevolver.trace.types import TraceEventType

        events = self._events_for(traj, step.step_number)
        seqs = [event.seq_no for event in events if event.seq_no is not None]
        if seqs:
            step.source_trace_seq_start = min(seqs)
            step.source_trace_seq_end = max(seqs)
        requests = [event for event in events
                    if event.event_type == TraceEventType.MODEL_REQUEST]
        if requests:
            # Retry and fallback each produce a request event. The final route is the
            # request that produced the decision (or the last failed attempt when the
            # step has no decision), so a trajectory must cite the last one.
            step.request_snapshot_id = requests[-1].metadata.get("request_snapshot_id")

    def _attach_run_trace_lineage(self, traj: Trajectory) -> None:
        """Cite the inclusive trace range from run start through its terminal event."""
        events = self._events_for(traj)
        seqs = [event.seq_no for event in events if event.seq_no is not None]
        if seqs:
            traj.source_trace_seq_start = min(seqs)
            traj.source_trace_seq_end = max(seqs)

    # ------------------------------------------------------------------
    # Reward — arrives after the run (benchmark judge / evaluator)
    # ------------------------------------------------------------------

    def set_reward(
        self,
        task_id: str,
        reward: float,
        *,
        evaluator: str = "external",
        evaluator_version: Optional[str] = None,
    ) -> Optional[str]:
        """Backfill a task-level reward to every step and re-persist.

        The run's ``task_id`` is surfaced on ``Response.data["task_id"]`` so a
        driver that runs an agent and then scores its output can correlate the
        two.

        Returns the task_id it matched, or ``None`` when there was no such trajectory.
        A caller holding a *session* id rather than a task id — the benchmark case — has
        no other way to tell "recorded" from "silently dropped", and a reward that goes
        nowhere leaves the corpus saying the run was worth zero.
        """
        traj = self._trajectories.get(task_id)
        if traj is None:
            logger.warning(f"| ⚠️ set_reward: no trajectory for task_id={task_id}")
            return None
        self._persist_reward_label(RewardLabel(
            session_id=traj.session_id,
            task_id=traj.task_id,
            reward=reward,
            evaluator=evaluator,
            evaluator_version=evaluator_version,
            source_trace_seq_start=traj.source_trace_seq_start,
            source_trace_seq_end=traj.source_trace_seq_end,
        ))
        traj.backfill_reward(reward)
        self._persist(traj)
        logger.info(f"| 🎯 Trajectory reward set: task_id={task_id} reward={reward}")
        return task_id

    def set_reward_by_session(
        self,
        session_id: str,
        reward: float,
        *,
        evaluator: str = "external",
        evaluator_version: Optional[str] = None,
    ) -> int:
        """Backfill reward to every trajectory in a session. Returns count matched.

        Convenience for the common one-task-per-session case (e.g. a benchmark
        that runs one agent session per task) where the caller holds the
        session id (``ctx.id``) rather than the run's task_id.
        """
        matched = [t for t in self._trajectories.values() if t.session_id == session_id]
        for traj in matched:
            self._persist_reward_label(RewardLabel(
                session_id=traj.session_id,
                task_id=traj.task_id,
                reward=reward,
                evaluator=evaluator,
                evaluator_version=evaluator_version,
                source_trace_seq_start=traj.source_trace_seq_start,
                source_trace_seq_end=traj.source_trace_seq_end,
            ))
            traj.backfill_reward(reward)
            self._persist(traj)
        if not matched:
            logger.warning(f"| ⚠️ set_reward_by_session: no trajectory for session_id={session_id}")
        else:
            logger.info(f"| 🎯 Trajectory reward set: session_id={session_id} reward={reward} ({len(matched)})")
        return len(matched)

    # ------------------------------------------------------------------
    # Query / export
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Optional[Trajectory]:
        return self._trajectories.get(task_id)

    def export_sft(self, task_id: str) -> List[Dict[str, Any]]:
        traj = self._authoritative_trajectory(task_id)
        return traj.to_sft_records() if traj else []

    def export_rl(self, task_id: str, fmt: Any) -> List[Dict[str, Any]]:
        traj = self._authoritative_trajectory(task_id)
        return traj.to_rl_records(fmt) if traj else []

    def _authoritative_trajectory(self, task_id: str) -> Optional[Trajectory]:
        """Prefer a complete trace projection; retain compatibility with old runs.

        Logs written before request snapshots cannot reconstruct ``messages_sent`` and
        must keep using their hook-built cache. New logs have a non-ignorable
        ``model_request`` per step; when that evidence is retained, exports rebuild from
        it so a missed trajectory hook cannot silently change the training row.
        """
        cached = self._trajectories.get(task_id)
        if cached is None:
            return None
        try:
            from agentevolver.trace.server import trace_manager
            from agentevolver.trace.types import TraceEventType

            events = [event for event in trace_manager.events(cached.session_id)
                      if event.task_id == task_id]
            if not any(event.event_type == TraceEventType.MODEL_REQUEST for event in events):
                return cached
            return self.rebuild_from_trace(events, task_id=task_id, adopt=False)
        except UnsupportedRewardLabel:
            # A newer evaluator schema may change the meaning of the score. Returning
            # the cached, older reward here would make an export look successful while
            # silently selecting superseded supervision, so incompatibility is fatal.
            raise
        except Exception as error:  # noqa: BLE001 — an export of an old cache still works
            logger.warning(
                f"| ⚠️ Trace projection failed for task_id={task_id}; "
                f"using trajectory cache: {error}"
            )
            return cached

    def rebuild_from_trace(
        self,
        events,
        *,
        task_id: Optional[str] = None,
        reward_labels: Optional[List[RewardLabel]] = None,
        adopt: bool = False,
    ) -> Trajectory:
        """Rebuild a trajectory from facts, optionally replacing the live cache.

        ``adopt=False`` makes verification side-effect free: callers can compare the
        projector with the hook-built cache without changing what exports currently read.
        Once equivalence has been proven across real runs, the same method is the switch
        that makes trace projection the normal read path.
        """
        selected = task_id
        if selected is None:
            ids = {event.task_id for event in events if event.task_id}
            selected = next(iter(ids)) if len(ids) == 1 else None
        labels = reward_labels
        if labels is None and selected:
            labels = self.load_reward_labels(selected)
        trajectory = project_trajectory(
            events, task_id=selected, reward_labels=labels or [],
        )
        if adopt:
            self._trajectories[trajectory.task_id] = trajectory
            self._persist(trajectory)
        return trajectory

    def rebuild_incrementally(
        self,
        session_id: str,
        *,
        task_id: Optional[str] = None,
        reward_labels: Optional[List[RewardLabel]] = None,
        adopt: bool = False,
        rebuild: bool = False,
        batch_size: int = 1000,
    ) -> Trajectory:
        """Resume a durable Trace projection without rescanning committed prefixes."""
        from agentevolver.trace.server import trace_manager

        if trace_manager.log_root is None or trace_manager.writer is None:
            raise RuntimeError("TraceManager must be initialized before incremental projection")
        labels = reward_labels
        if labels is None and task_id:
            labels = self.load_reward_labels(task_id)
        from agentevolver.trace.projection import get_default_projection_registry

        # Resolve through the same versioned registry as stats/search/UI consumers. A
        # new projection is therefore discoverable without adding another hard-coded
        # construction path to the manager facade.
        projector = get_default_projection_registry().create(
            "trajectory", trace_manager.writer, trace_manager.log_root,
        )
        trajectory = projector.project(
            session_id,
            task_id=task_id,
            reward_labels=labels or [],
            batch_size=batch_size,
            rebuild=rebuild,
        )
        if adopt:
            self._trajectories[trajectory.task_id] = trajectory
            self._persist(trajectory)
        return trajectory

    # ------------------------------------------------------------------
    # Reading back — a run that has already ended
    # ------------------------------------------------------------------

    def load(self, path: str) -> Optional[Trajectory]:
        """Read one persisted trajectory back into memory.

        `_persist` wrote these and nothing read them. Every export — `export_sft`,
        `export_rl` — looks in `self._trajectories`, which holds only what *this* process
        built, so the moment a run ended the data it produced could no longer be turned
        into the training records the module exists to produce. The JSONL was an artifact
        nothing could open.

        Line 1 is the header, the rest are steps, exactly as `_persist` writes them. A
        line that will not parse is skipped rather than failing the file: a truncated
        tail is the normal shape of a log from a run that was killed, and losing the
        whole trajectory over its last line would discard every step before it.
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line.strip()]
        except OSError as error:
            logger.warning(f"| ⚠️ Could not read trajectory {path}: {error}")
            return None
        if not lines:
            return None

        try:
            header = json.loads(lines[0])
        except ValueError as error:
            logger.warning(f"| ⚠️ Trajectory {path} has no readable header: {error}")
            return None
        header.pop("__header__", None)
        # Files written before versioning are the original schema, not silently upgraded
        # to the current one merely because Pydantic supplied a default.
        header.setdefault("schema_version", 1)
        try:
            stored_version = int(header["schema_version"])
        except (TypeError, ValueError):
            logger.warning(f"| ⚠️ Trajectory {path} has an invalid schema_version")
            return None
        if stored_version > TRAJECTORY_SCHEMA_VERSION:
            logger.warning(
                f"| ⚠️ Trajectory {path} uses schema {stored_version}; this reader "
                f"supports up to {TRAJECTORY_SCHEMA_VERSION}"
            )
            return None

        steps: List[TrajectoryStep] = []
        for index, line in enumerate(lines[1:], start=2):
            try:
                steps.append(TrajectoryStep(**json.loads(line)))
            except (ValueError, TypeError) as error:
                logger.warning(f"| ⚠️ {path} line {index} skipped: {error}")
        try:
            return Trajectory(**header, steps=steps)
        except (ValueError, TypeError) as error:
            logger.warning(f"| ⚠️ Trajectory {path} header does not fit: {error}")
            return None

    def load_all(self, directory: Optional[str] = None) -> List[Trajectory]:
        """Every trajectory under a directory, newest last.

        Defaults to this manager's own persistence root, so the common case — "give me
        what this project has recorded" — needs no path. Ordering is by name, which for
        these files is by task id rather than by time; a caller that needs chronology
        should sort on the header rather than trusting the directory.
        """
        root = directory or self.base_dir
        if not root or not os.path.isdir(root):
            return []
        loaded = []
        for name in sorted(os.listdir(root)):
            if not name.endswith(".jsonl"):
                continue
            trajectory = self.load(str(path_manager.resolve_under(root, name)))
            if trajectory is not None:
                loaded.append(trajectory)
        return loaded

    # ------------------------------------------------------------------
    # Reward labels — append-only evaluator facts
    # ------------------------------------------------------------------

    def _label_path(self, label: RewardLabel) -> str:
        base = self.base_dir or self._default_base_dir()
        session_key = self._label_key(label.session_id)
        task_key = self._label_key(label.task_id)
        labels = path_manager.resolve_under(base, "labels")
        return str(path_manager.resolve_under(
            labels, f"{session_key}__{task_key}.jsonl",
        ))

    @staticmethod
    def _label_key(value: str) -> str:
        """A path-safe identity without the collisions introduced by slash replacement."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _persist_reward_label(self, label: RewardLabel) -> None:
        """Append one score before rewriting the trajectory cache.

        A second evaluation is a new fact, not an edit of the first one. The trajectory
        header still carries the latest reward for backward-compatible exports, while the
        sidecar preserves who scored it and what value it replaced.
        """
        try:
            path = self._label_path(label)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(label.to_dict(), ensure_ascii=False) + "\n")
        except Exception as error:  # noqa: BLE001 — scoring cannot break benchmark output
            logger.warning(
                f"| ⚠️ Reward label persist failed (task_id={label.task_id}): {error}"
            )

    def load_reward_labels(self, task_id: str) -> List[RewardLabel]:
        """All readable labels for a task, preserving append order across its files."""
        base = self.base_dir or self._default_base_dir()
        directory = str(path_manager.resolve_under(base, "labels"))
        if not os.path.isdir(directory):
            return []
        task_key = self._label_key(task_id)
        labels: List[RewardLabel] = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(f"__{task_key}.jsonl"):
                continue
            path = str(path_manager.resolve_under(directory, name))
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
            except OSError as error:
                logger.warning(f"| ⚠️ Could not read reward labels {path}: {error}")
                continue
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    version = int(payload.get("schema_version", 1))
                    if version > REWARD_LABEL_SCHEMA_VERSION:
                        # Falling back to an older label would silently select the wrong
                        # reward. This is a semantic incompatibility, not a torn tail.
                        raise UnsupportedRewardLabel(
                            f"unsupported reward label schema {version} in {path}"
                        )
                    labels.append(RewardLabel(**payload))
                except UnsupportedRewardLabel:
                    raise
                except (TypeError, ValueError) as error:
                    logger.warning(f"| ⚠️ {path} line {number} skipped: {error}")
        return labels


    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _path(self, traj: Trajectory) -> str:
        # Falling back to `.` put `<task_id>.jsonl` in whatever directory the process
        # happened to start in — for a run launched from a checkout, the repository root.
        # The hook that writes these is registered by `hook_manager.initialize()`, so any
        # runner that does not also initialize *this* manager was leaking files into the
        # source tree; `examples/run_browser_agent.py` did, and so did every new runner
        # written from it. The default now resolves the same way `initialize()` does.
        base = self.base_dir or self._default_base_dir()
        safe = traj.task_id.replace("/", "_").replace("\\", "_")
        return str(path_manager.resolve_under(base, f"{safe}.jsonl"))

    @staticmethod
    def _default_base_dir() -> str:
        from agentevolver.config import config
        from agentevolver.utils import assemble_workspace_path

        try:
            log_root = config.log_root
        except (AttributeError, KeyError):
            log_root = "workspace_root"
        return assemble_workspace_path(path_manager.under(log_root, P.LOG_MODULE, module="trajectory"))

    def _persist(self, traj: Trajectory) -> None:
        try:
            path = self._path(traj)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            d = traj.to_dict()
            steps = d.pop("steps")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"__header__": True, **d}, ensure_ascii=False) + "\n")
                for step in steps:
                    f.write(json.dumps(step, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"| ⚠️ Trajectory persist failed (task_id={traj.task_id}): {e}")


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


# Global singleton — import this everywhere
trajectory_manager = TrajectoryManagerServer()
