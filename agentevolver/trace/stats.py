"""Crash-safe operational statistics projected from committed Trace events.

This module intentionally derives telemetry from Trace instead of adding counters to the
runtime hot path. The same replay therefore produces the same report, and rebuilding a
dashboard never changes agent behaviour or training evidence.
"""

from __future__ import annotations

import inspect
import json
import os
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.model.types import TokenUsage
from agentevolver.trace.projection import ProjectionWatermarkError, ProjectionWatermarkStore
from agentevolver.trace.types import TraceEvent, TraceEventType, parse_trace_event


PROJECTOR_VERSION = 1
PROJECTION_NAME = "stats"
STATS_SCHEMA_VERSION = 1


class TraceStats(BaseModel):
    """Small, merge-friendly summary for one Trace session.

    ``usage`` sums step-level ``agent_call`` usage. ``reported_run_usage`` is the
    independently reported total on the latest ``agent_end`` event; keeping both avoids
    double-counting the terminal total while making incomplete step capture detectable.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=STATS_SCHEMA_VERSION)
    projector_version: int = Field(default=PROJECTOR_VERSION)
    session_id: str
    source_last_seq: int = -1
    event_count: int = 0
    event_counts: Dict[str, int] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    agent_names: list[str] = Field(default_factory=list)
    model_routes: Dict[str, int] = Field(default_factory=dict)
    providers: Dict[str, int] = Field(default_factory=dict)
    tool_calls: Dict[str, int] = Field(default_factory=dict)
    skill_calls: Dict[str, int] = Field(default_factory=dict)
    successful_actions: int = 0
    failed_actions: int = 0
    error_events: int = 0
    duration_ms_by_event: Dict[str, float] = Field(default_factory=dict)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    reported_run_usage: Optional[TokenUsage] = None
    terminal_success: Optional[bool] = None
    first_event_at: Optional[str] = None
    last_event_at: Optional[str] = None


def _increment(values: Dict[str, int], key: str) -> None:
    if key:
        values[key] = int(values.get(key, 0)) + 1


class TraceStatsProjector:
    """Incrementally reduce committed events into a compact atomic checkpoint."""

    projection_name = PROJECTION_NAME
    projection_version = PROJECTOR_VERSION

    def __init__(self, trace_reader: Any, trace_root: str) -> None:
        self.trace_reader = trace_reader
        self.watermarks = ProjectionWatermarkStore(trace_root)

    def _state_path(self, session_id: str) -> str:
        return self.watermarks.path(PROJECTION_NAME, session_id) + ".state.json"

    def reset(self, session_id: str) -> None:
        self.watermarks.reset(PROJECTION_NAME, session_id)
        path = self._state_path(session_id)
        if os.path.exists(path):
            os.remove(path)

    def _load_state(self, session_id: str) -> Optional[TraceStats]:
        path = self._state_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = TraceStats.model_validate(json.load(handle))
        except Exception as exc:  # noqa: BLE001 - partial metrics are misleading
            raise ProjectionWatermarkError(
                f"cannot read stats projection state {path}: {exc}"
            ) from exc
        if state.schema_version != STATS_SCHEMA_VERSION:
            raise ProjectionWatermarkError(
                f"stats state schema {state.schema_version} is unsupported"
            )
        if state.projector_version != PROJECTOR_VERSION:
            raise ProjectionWatermarkError(
                f"stats state uses projector {state.projector_version}; rebuild required"
            )
        if state.session_id != session_id:
            raise ProjectionWatermarkError("stats projection state identity mismatch")
        return state

    def _save_state(self, state: TraceStats) -> None:
        path = self._state_path(state.session_id)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        temporary = path + f".tmp-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(
                    state.model_dump(mode="json"), handle,
                    ensure_ascii=False, sort_keys=True, indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def _read_from(self, session_id: str, after_seq: int, limit: int) -> list[Any]:
        parameters = inspect.signature(self.trace_reader.read_from).parameters.values()
        accepts_durable = any(
            parameter.name == "durable" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        kwargs: Dict[str, Any] = {"after_seq": after_seq, "limit": limit}
        if accepts_durable:
            kwargs["durable"] = True
        return self.trace_reader.read_from(session_id, **kwargs)

    @staticmethod
    def _event(value: Any) -> Optional[TraceEvent]:
        if isinstance(value, TraceEvent):
            return value
        if isinstance(value, dict):
            return parse_trace_event(value)
        raise ProjectionWatermarkError(
            f"trace reader returned unsupported event value {type(value).__name__}"
        )

    @staticmethod
    def _add_usage(total: TokenUsage, raw: Optional[Dict[str, Any]]) -> TokenUsage:
        usage = TokenUsage.from_raw(raw)
        if usage is None:
            return total
        return TokenUsage(
            input_tokens=total.input_tokens + usage.input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
            cache_write_tokens=total.cache_write_tokens + usage.cache_write_tokens,
            cache_read_tokens=total.cache_read_tokens + usage.cache_read_tokens,
            cost=(
                None if total.cost is None and usage.cost is None
                else float(total.cost or 0.0) + float(usage.cost or 0.0)
            ),
        )

    def _reduce(self, state: TraceStats, event: TraceEvent) -> None:
        event_type = event.event_type.value
        state.event_count += 1
        _increment(state.event_counts, event_type)
        if event.task_id and event.task_id not in state.task_ids:
            state.task_ids.append(event.task_id)
            state.task_ids.sort()
        if event.agent_name and event.agent_name not in state.agent_names:
            state.agent_names.append(event.agent_name)
            state.agent_names.sort()

        timestamp = event.timestamp.isoformat()
        state.first_event_at = min(state.first_event_at, timestamp) if state.first_event_at else timestamp
        state.last_event_at = max(state.last_event_at, timestamp) if state.last_event_at else timestamp
        if event.duration_ms is not None:
            state.duration_ms_by_event[event_type] = (
                float(state.duration_ms_by_event.get(event_type, 0.0)) + float(event.duration_ms)
            )

        if event.event_type == TraceEventType.MODEL_REQUEST:
            snapshot = event.input or {}
            _increment(state.model_routes, str(snapshot.get("routed_model") or "unknown"))
            _increment(state.providers, str(snapshot.get("provider") or "unknown"))
        elif event.event_type in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            target = state.tool_calls if event.event_type == TraceEventType.TOOL_CALL else state.skill_calls
            _increment(target, event.action_name or "unknown")
            if event.success is True:
                state.successful_actions += 1
            else:
                state.failed_actions += 1
        elif event.event_type == TraceEventType.ERROR:
            state.error_events += 1

        # AGENT_END repeats the run total accumulated from AGENT_CALL events. It is a
        # reconciliation value, not another billable request.
        if event.event_type == TraceEventType.AGENT_CALL:
            state.usage = self._add_usage(state.usage, event.usage)
        elif event.event_type == TraceEventType.AGENT_END:
            state.reported_run_usage = TokenUsage.from_raw(event.usage)
            state.terminal_success = event.success
        elif event.event_type == TraceEventType.WORKFLOW_END:
            state.terminal_success = event.success

    def project(
        self,
        session_id: str,
        *,
        batch_size: int = 1000,
        rebuild: bool = False,
    ) -> TraceStats:
        """Advance the compact state exactly once per committed source sequence."""
        if rebuild:
            self.reset(session_id)
        watermark = self.watermarks.load(PROJECTION_NAME, PROJECTOR_VERSION, session_id)
        state = self._load_state(session_id)
        if watermark is not None and state is None:
            raise ProjectionWatermarkError(
                "stats watermark exists without reducer state; rebuild required"
            )
        state = state or TraceStats(session_id=session_id)
        if watermark is not None and state.source_last_seq < watermark.last_seq:
            raise ProjectionWatermarkError(
                "stats reducer state is behind its watermark; rebuild required"
            )
        # A crash after the atomic checkpoint but before watermark replacement leaves
        # state ahead. The checkpoint is already durable, so reconcile the cursor rather
        # than replaying and double-counting its events.
        if watermark is None or state.source_last_seq > watermark.last_seq:
            if state.source_last_seq >= 0:
                watermark = self.watermarks.advance(
                    PROJECTION_NAME, PROJECTOR_VERSION, session_id,
                    state.source_last_seq,
                )

        cursor = state.source_last_seq
        limit = max(1, int(batch_size))
        while True:
            batch = self._read_from(session_id, cursor, limit)
            if not batch:
                break
            source_seqs = []
            parsed = []
            for item in batch:
                raw_seq = item.seq_no if isinstance(item, TraceEvent) else item.get("seq_no")
                try:
                    source_seqs.append(int(raw_seq))
                except (TypeError, ValueError) as exc:
                    raise ProjectionWatermarkError(
                        "incremental trace batch contains an invalid sequence number"
                    ) from exc
                parsed.append(self._event(item))
            batch_end = max(source_seqs)
            for event in parsed:
                if event is not None:
                    self._reduce(state, event)
            state.source_last_seq = batch_end
            self._save_state(state)
            self.watermarks.advance(
                PROJECTION_NAME, PROJECTOR_VERSION, session_id, batch_end,
            )
            cursor = batch_end
            if len(batch) < limit:
                break
        return state.model_copy(deep=True)


__all__ = [
    "PROJECTOR_VERSION",
    "PROJECTION_NAME",
    "STATS_SCHEMA_VERSION",
    "TraceStats",
    "TraceStatsProjector",
]
