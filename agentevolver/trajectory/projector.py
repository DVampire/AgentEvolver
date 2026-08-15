"""Deterministically rebuild a trainable trajectory from trace facts and reward labels.

The live hook writer remains a low-latency cache. This projector is the correctness path:
if deleting that cache changes the training record, the trace contract is incomplete.
"""

from __future__ import annotations

import json
import inspect
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.trace.projection import ProjectionWatermarkError, ProjectionWatermarkStore
from agentevolver.trace.types import (
    TRACE_FORMAT_VERSION,
    TraceEvent,
    TraceEventType,
    parse_trace_event,
)
from agentevolver.trajectory.labels import RewardLabel
from agentevolver.trajectory.types import Trajectory, TrajectoryStep


PROJECTOR_VERSION = 1
PROJECTION_NAME = "trajectory"
PROJECTION_STATE_VERSION = 1


class TrajectoryProjectionState(BaseModel):
    """Idempotent reducer state saved before its source watermark advances."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=PROJECTION_STATE_VERSION)
    projector_version: int = Field(default=PROJECTOR_VERSION)
    session_id: str
    source_last_seq: int = -1
    events: List[Dict[str, Any]] = Field(default_factory=list)


_PROJECTED_EVENT_TYPES = {
    TraceEventType.AGENT_START,
    TraceEventType.MODEL_REQUEST,
    TraceEventType.TOOL_START,
    TraceEventType.SKILL_START,
    TraceEventType.TOOL_CALL,
    TraceEventType.SKILL_CALL,
    TraceEventType.AGENT_CALL,
    TraceEventType.AGENT_END,
}


class IncrementalTrajectoryProjector:
    """Consume committed Trace suffixes and resume from a durable watermark."""

    projection_name = PROJECTION_NAME
    projection_version = PROJECTOR_VERSION

    def __init__(self, trace_reader: Any, trace_root: str) -> None:
        self.trace_reader = trace_reader
        self.watermarks = ProjectionWatermarkStore(trace_root)

    def _state_path(self, session_id: str) -> str:
        return self.watermarks.path(PROJECTION_NAME, session_id) + ".state.jsonl"

    def reset(self, session_id: str) -> None:
        self.watermarks.reset(PROJECTION_NAME, session_id)
        path = self._state_path(session_id)
        if os.path.exists(path):
            os.remove(path)

    def _load_state(self, session_id: str) -> Optional[TrajectoryProjectionState]:
        path = self._state_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw_lines = handle.readlines()
        except Exception as exc:  # noqa: BLE001 - corrupt state cannot be skipped safely
            raise ProjectionWatermarkError(
                f"cannot read trajectory projection state {path}: {exc}"
            ) from exc
        if not raw_lines:
            raise ProjectionWatermarkError("trajectory projection state is empty")
        try:
            header = json.loads(raw_lines[0])
        except json.JSONDecodeError as exc:
            raise ProjectionWatermarkError("trajectory projection header is corrupt") from exc
        if header.get("kind") != "trajectory_projection_state":
            raise ProjectionWatermarkError("trajectory projection state has no header")
        if int(header.get("schema_version", 0)) != PROJECTION_STATE_VERSION:
            raise ProjectionWatermarkError(
                f"trajectory state schema {header.get('schema_version')} is unsupported"
            )
        if int(header.get("projector_version", 0)) != PROJECTOR_VERSION:
            raise ProjectionWatermarkError(
                f"trajectory state uses projector {header.get('projector_version')}; "
                "rebuild required"
            )
        if header.get("session_id") != session_id:
            raise ProjectionWatermarkError("trajectory projection state identity mismatch")
        committed: Dict[int, Dict[str, Any]] = {}
        pending: List[Dict[str, Any]] = []
        source_last_seq = -1
        for offset, line in enumerate(raw_lines[1:], start=1):
            index = offset + 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                # A killed append may leave only its final line torn. No batch marker
                # follows it, so none of that batch is committed.
                if offset == len(raw_lines) - 1:
                    # Remove the torn bytes now. Otherwise a later append would put a
                    # valid batch after them, turning an understood torn tail into
                    # apparent middle-of-file corruption on the next restart.
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.writelines(raw_lines[:offset])
                        handle.flush()
                        os.fsync(handle.fileno())
                    break
                raise ProjectionWatermarkError(
                    f"trajectory projection state line {index} is corrupt"
                ) from exc
            kind = record.get("kind")
            if kind == "event":
                pending.append(record.get("event") or {})
            elif kind == "batch":
                for payload in pending:
                    committed[int(payload["seq_no"])] = payload
                pending = []
                source_last_seq = int(record["source_last_seq"])
            else:
                raise ProjectionWatermarkError(
                    f"trajectory projection state line {index} has unknown kind {kind!r}"
                )
        return TrajectoryProjectionState(
            session_id=session_id,
            source_last_seq=source_last_seq,
            events=[committed[seq] for seq in sorted(committed)],
        )

    def _append_batch(
        self,
        session_id: str,
        events: List[Dict[str, Any]],
        source_last_seq: int,
    ) -> None:
        """Append facts followed by a commit marker; a torn batch is ignored on load."""
        path = self._state_path(session_id)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        created = not os.path.exists(path)
        records = []
        if created:
            records.append({
                "kind": "trajectory_projection_state",
                "schema_version": PROJECTION_STATE_VERSION,
                "projector_version": PROJECTOR_VERSION,
                "session_id": session_id,
            })
        records.extend({"kind": "event", "event": event} for event in events)
        records.append({"kind": "batch", "source_last_seq": int(source_last_seq)})
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for record in records
        )
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    def _read_from(self, session_id: str, after_seq: int, limit: int) -> list[Any]:
        parameters = inspect.signature(self.trace_reader.read_from).parameters.values()
        accepts_durable = any(
            parameter.name == "durable" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if accepts_durable:
            return self.trace_reader.read_from(
                session_id, after_seq=after_seq, limit=limit, durable=True,
            )
        return self.trace_reader.read_from(
            session_id, after_seq=after_seq, limit=limit,
        )

    @staticmethod
    def _event(value: Any) -> Optional[TraceEvent]:
        if isinstance(value, TraceEvent):
            return value
        if isinstance(value, dict):
            return parse_trace_event(value)
        raise ProjectionWatermarkError(
            f"trace reader returned unsupported event value {type(value).__name__}"
        )

    def project(
        self,
        session_id: str,
        *,
        task_id: Optional[str] = None,
        reward_labels: Iterable[RewardLabel] = (),
        batch_size: int = 1000,
        rebuild: bool = False,
    ) -> Trajectory:
        """Advance reducer state in batches, then materialize one trajectory.

        State is fsynced before its watermark. A crash between those writes replays the
        same source events, which are deduplicated by ``seq_no``; reversing that order
        would skip facts permanently.
        """
        if rebuild:
            self.reset(session_id)
        watermark = self.watermarks.load(PROJECTION_NAME, PROJECTOR_VERSION, session_id)
        state = self._load_state(session_id)
        if watermark is not None and state is None:
            raise ProjectionWatermarkError(
                "trajectory watermark exists without reducer state; rebuild required"
            )
        state = state or TrajectoryProjectionState(session_id=session_id)
        if watermark is not None and state.source_last_seq < watermark.last_seq:
            raise ProjectionWatermarkError(
                "trajectory reducer state is behind its watermark; rebuild required"
            )

        cursor = watermark.last_seq if watermark is not None else -1
        limit = max(1, int(batch_size))
        while True:
            batch = self._read_from(session_id, cursor, limit)
            if not batch:
                break
            raw_seqs = [
                item.seq_no if isinstance(item, TraceEvent) else item.get("seq_no")
                for item in batch
            ]
            try:
                source_seqs = [int(seq) for seq in raw_seqs if seq is not None]
            except (TypeError, ValueError) as exc:
                raise ProjectionWatermarkError(
                    "incremental trace batch contains an invalid sequence number"
                ) from exc
            if not source_seqs:
                raise ProjectionWatermarkError(
                    "incremental trace batch has no sequence numbers"
                )
            parsed = [self._event(item) for item in batch]
            events = [event for event in parsed if event is not None]
            batch_end = max(source_seqs)
            known = {int(item["seq_no"]) for item in state.events}
            added = []
            for event in events:
                if event.seq_no in known or event.event_type not in _PROJECTED_EVENT_TYPES:
                    continue
                payload = event.to_dict()
                state.events.append(payload)
                added.append(payload)
                known.add(int(event.seq_no))
            state.events.sort(key=lambda item: int(item["seq_no"]))
            self._append_batch(session_id, added, batch_end)
            state.source_last_seq = batch_end
            self.watermarks.advance(
                PROJECTION_NAME, PROJECTOR_VERSION, session_id, batch_end,
            )
            cursor = batch_end
            if len(batch) < limit:
                break

        projected_events = [
            event for event in (parse_trace_event(payload) for payload in state.events)
            if event is not None
        ]
        return project_trajectory(
            projected_events, task_id=task_id, reward_labels=reward_labels,
        )


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _call_id(event: Any) -> str:
    explicit = (event.metadata or {}).get("call_id")
    if explicit:
        return str(explicit)
    return f"step{event.step_number or 0}_call{event.action_index or 0}"


def _latest_reward(labels: Iterable[RewardLabel], task_id: str) -> float:
    matching = [label for label in labels
                if label.task_id == task_id and label.granularity == "task"]
    if not matching:
        return 0.0
    # Timestamps are part of the immutable label, so file order is not required for a
    # deterministic answer after labels from several stores are merged.
    return max(matching, key=lambda label: (label.timestamp, label.label_id)).reward


def project_trajectory(
    events: Sequence[Any],
    *,
    reward_labels: Iterable[RewardLabel] = (),
    task_id: Optional[str] = None,
) -> Trajectory:
    """Build one trajectory solely from committed trace events and immutable labels.

    Args:
        events: One session's trace in sequence order. Events for sibling tasks may be
            present; ``task_id`` selects one when needed.
        reward_labels: Append-only evaluator outputs. The newest task-level label wins.
        task_id: Task to project; inferred only when the events name exactly one.

    Raises:
        ValueError: The task cannot be selected or lacks a start event. Refusing a partial
            episode is safer than returning a plausible record with no task provenance.
    """
    ordered = sorted(
        [event for event in events if event.seq_no is not None],
        key=lambda event: event.seq_no,
    )
    task_ids = {event.task_id for event in ordered if event.task_id}
    selected = task_id or (next(iter(task_ids)) if len(task_ids) == 1 else None)
    if not selected:
        raise ValueError("task_id is required when trace contains zero or several tasks")
    selected_events = [event for event in ordered if event.task_id == selected]
    start = next(
        (event for event in selected_events
         if event.event_type == TraceEventType.AGENT_START),
        None,
    )
    if start is None:
        raise ValueError(f"trace has no agent_start for task {selected}")

    session_id = start.session_id or ""
    reward = _latest_reward(reward_labels, selected)
    by_step: Dict[int, List[Any]] = {}
    for event in selected_events:
        if event.step_number is not None:
            by_step.setdefault(event.step_number, []).append(event)

    steps: List[TrajectoryStep] = []
    for number in sorted(by_step):
        facts = by_step[number]
        close = next(
            (event for event in reversed(facts)
             if event.event_type == TraceEventType.AGENT_CALL),
            None,
        )
        if close is None:
            continue
        requests = [event for event in facts
                    if event.event_type == TraceEventType.MODEL_REQUEST]
        request = requests[-1] if requests else None
        starts = [event for event in facts if event.event_type in (
            TraceEventType.TOOL_START, TraceEventType.SKILL_START,
        )]
        results = [event for event in facts if event.event_type in (
            TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL,
        )]
        starts_by_id = {_call_id(event): event for event in starts}

        # Code Mode sub-calls are real executions and their results stay in observations,
        # but the model emitted only the outer program call. Training on every nested
        # call as an assistant action would teach output the provider never produced.
        model_starts = [event for event in starts
                        if not (event.metadata or {}).get("parent_call_id")]
        actions = [{
            "index": event.action_index,
            "id": _call_id(event),
            "type": event.action_type,
            "name": event.action_name,
            "args": event.input or {},
        } for event in model_starts]
        observations = []
        for result in results:
            origin = starts_by_id.get(_call_id(result))
            observations.append({
                "index": result.action_index,
                "type": result.action_type,
                "name": result.action_name,
                "args": (origin.input if origin is not None else None),
                "result": _stringify(result.output),
                "error": result.error,
            })

        seqs = [event.seq_no for event in facts if event.seq_no is not None]
        usage = close.usage
        steps.append(TrajectoryStep(
            step_number=number,
            messages_sent=((request.input or {}).get("messages", []) if request else []),
            reasoning=close.reasoning or "",
            actions=actions,
            observations=observations,
            token_usage=int((usage or {}).get("output_tokens") or 0),
            usage=usage,
            reward=reward,
            request_snapshot_id=(
                request.metadata.get("request_snapshot_id") if request else None
            ),
            source_trace_seq_start=min(seqs) if seqs else None,
            source_trace_seq_end=max(seqs) if seqs else None,
        ))

    end = next(
        (event for event in reversed(selected_events)
         if event.event_type == TraceEventType.AGENT_END),
        None,
    )
    seqs = [event.seq_no for event in selected_events if event.seq_no is not None]
    metadata = {
        key: value for key, value in {
            "parent_session_id": (start.metadata or {}).get("parent_session_id"),
            "subtask_id": (start.metadata or {}).get("subtask_id"),
            "projector_version": PROJECTOR_VERSION,
        }.items() if value not in (None, "")
    }
    return Trajectory(
        session_id=session_id,
        task_id=selected,
        agent_name=start.agent_name or "",
        task_description=_stringify((start.input or {}).get("task")) or "",
        steps=steps,
        success=bool(end.success) if end is not None else False,
        final_result=_stringify(end.output) if end is not None else None,
        reward=reward,
        metadata=metadata,
        source_trace_format_version=TRACE_FORMAT_VERSION,
        source_trace_seq_start=min(seqs) if seqs else None,
        source_trace_seq_end=max(seqs) if seqs else None,
    )


__all__ = [
    "PROJECTOR_VERSION",
    "PROJECTION_NAME",
    "PROJECTION_STATE_VERSION",
    "TrajectoryProjectionState",
    "IncrementalTrajectoryProjector",
    "project_trajectory",
]
