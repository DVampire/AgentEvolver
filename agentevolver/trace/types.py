"""TraceEvent type hierarchy — every agent execution step produces one of these."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, ValidationError

from agentevolver.trace.surface import APPEND
from agentevolver.utils import make_id


TRACE_FORMAT_VERSION = 1


class UnsupportedTraceEvent(ValueError):
    """A trace line cannot be interpreted without changing the reconstructed truth."""


class TraceEventType(str, Enum):
    """Agent / tool / skill lifecycle events, plus error."""

    # Agent lifecycle
    AGENT_START = "agent_start"   # agent begins
    AGENT_CALL  = "agent_call"    # mid-execution step / state update
    AGENT_END   = "agent_end"     # agent finishes (success or failure)

    # Model lifecycle. Unlike AGENT_CALL (the assistant decision after a step), this is
    # the exact request state committed immediately before provider dispatch.
    MODEL_REQUEST = "model_request"

    # Tool lifecycle
    TOOL_START  = "tool_start"
    TOOL_CALL   = "tool_call"     # tool result received
    TOOL_END    = "tool_end"

    # Skill lifecycle
    SKILL_START = "skill_start"
    SKILL_CALL  = "skill_call"    # skill result received
    SKILL_END   = "skill_end"

    # Deterministic orchestration lifecycle
    WORKFLOW_START = "workflow_start"
    WORKFLOW_NODE_START = "workflow_node_start"
    WORKFLOW_NODE_END = "workflow_node_end"
    WORKFLOW_FRAME_START = "workflow_frame_start"
    WORKFLOW_FRAME_END = "workflow_frame_end"
    WORKFLOW_END = "workflow_end"

    # Misc
    ERROR       = "error"
    CUSTOM      = "custom"


class EventProvenance(str, Enum):
    """Where this event originated."""
    LIVE        = "live"
    TEST        = "test"
    REPLAY      = "replay"
    HEALTHCHECK = "healthcheck"


class EventConfidence(str, Enum):
    """Confidence level — gates automated downstream actions."""
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class TraceEvent(BaseModel):
    """A single, immutable trace record produced during agent execution."""

    #: Version of the event envelope, independent from the event vocabulary. Old logs
    #: have no field and validate as version 1, preserving backward compatibility.
    schema_version: int = Field(default=TRACE_FORMAT_VERSION)
    #: Whether a reader that does not recognise this event may skip it safely. Request
    #: snapshots are non-ignorable because skipping one changes training provenance;
    #: observational custom events may explicitly opt in.
    ignorable: bool = Field(default=False)
    id: str = Field(default_factory=lambda: make_id())
    event_type: TraceEventType

    session_id:   Optional[str] = None
    task_id:      Optional[str] = None
    agent_name:   Optional[str] = None

    label: str = Field(default="")

    step_number:  Optional[int] = None
    action_index: Optional[int] = None
    action_type:  Optional[str] = None   # "tool" | "skill"
    action_name:  Optional[str] = None

    input:    Optional[Dict[str, Any]] = Field(default=None)
    output:   Optional[Any]            = Field(default=None)
    reasoning: Optional[str]           = Field(default=None)
    #: Model-visible assistant text. ``reasoning`` may be private provider thinking and
    #: must not be replayed as ordinary assistant prose when this field is present.
    assistant_text: Optional[str]      = Field(default=None)
    provider_state: Dict[str, Any]     = Field(default_factory=dict)
    message:  Optional[str]            = Field(default=None)
    success:  Optional[bool]           = None
    error:    Optional[str]            = None

    duration_ms: Optional[float] = None

    #: What this call cost, as ``TokenUsage.model_dump()`` — input / output /
    #: cache_write / cache_read / cost. A first-class field rather than a corner of
    #: ``metadata`` because it is the only durable record of whether a prompt was
    #: cached: the counts exist in memory on every provider path, and until they are
    #: written here nothing downstream can tell a cache hit from a full re-read.
    usage: Optional[Dict[str, Any]] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    #: Position in this session's log, stamped by ``trace_manager.emit``. Contiguous
    #: from 0 and continued across restarts from the writer's index, so it names an
    #: event unambiguously for anything that needs to cite one.
    seq_no:      Optional[int] = Field(default=None)

    #: How this event joins the *surface* — the ordered subset of the log that stands
    #: for the session's history. ``"append"`` adds it at the tail;
    #: ``{"op": "replace", "start": s, "end": e}`` puts it in place of the surface
    #: entries from ``s`` through ``e`` inclusive.
    #:
    #: Replacement is what lets compaction be recorded without being destructive: the
    #: summarised events stay in the log exactly as they were written, and the summary
    #: shadows them. One log, two readings — the surface for what the history now says,
    #: the raw append order for what actually happened.
    surface_op: Optional[Union[str, Dict[str, Any]]] = Field(default=None)

    #: Seq numbers this event was derived from. A replacement MUST cite every surface
    #: entry it shadows, so a reader can always recover the originals behind a summary.
    source_event_seqs: Optional[List[int]] = Field(default=None)

    fingerprint: Optional[str] = Field(default=None)
    provenance:  EventProvenance  = Field(default=EventProvenance.LIVE)
    confidence:  EventConfidence  = Field(default=EventConfidence.HIGH)

    def to_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["timestamp"] = self.timestamp.isoformat()
        return d


def parse_trace_event(payload: Dict[str, Any]) -> Optional[TraceEvent]:
    """Validate one event under the explicit compatibility contract.

    Unknown ignorable vocabulary may be skipped, but an unknown envelope version or a
    non-ignorable event is refused. This distinction prevents the most dangerous reader
    behaviour: returning a well-formed, incomplete training projection.
    """
    try:
        version = int(payload.get("schema_version", 1))
    except (TypeError, ValueError) as error:
        raise UnsupportedTraceEvent("trace event has an invalid schema_version") from error
    if version < 1 or version > TRACE_FORMAT_VERSION:
        raise UnsupportedTraceEvent(
            f"trace envelope {version} is unsupported; reader supports "
            f"1..{TRACE_FORMAT_VERSION}"
        )
    try:
        return TraceEvent.model_validate(payload)
    except ValidationError as error:
        if payload.get("ignorable") is True:
            return None
        raise UnsupportedTraceEvent(
            f"non-ignorable trace event cannot be parsed: {payload.get('event_type')!r}"
        ) from error


def compute_event_fingerprint(event: TraceEvent) -> str:
    import hashlib
    parts = [
        event.event_type.value,
        event.session_id or "",
        str(event.step_number or ""),
        str(event.action_index or ""),
        event.action_name or "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def model_request_event(
    session_id: str,
    snapshot: "Any",
    *,
    task_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    step_number: Optional[int] = None,
    attempt: int = 1,
    route_index: int = 0,
) -> TraceEvent:
    """Record the request facts before anything crosses the provider boundary.

    The full snapshot lives in ``input`` because it is durable evidence, not display
    metadata. Its content-addressed id is repeated in metadata so projections can locate
    it without deserializing the entire request.
    """
    snapshot_dict = snapshot.model_dump(mode="json")
    return TraceEvent(
        event_type=TraceEventType.MODEL_REQUEST,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        label=f"model request: {snapshot.routed_model}",
        input=snapshot_dict,
        metadata={
            "type": "model_request",
            "request_snapshot_id": snapshot.snapshot_id,
            "attempt": attempt,
            "route_index": route_index,
        },
        # A reader that drops this event can still render a conversation, but cannot
        # truthfully rebuild the request or its training lineage.
        ignorable=False,
    )


# ---------------------------------------------------------------------------
# Event constructors
# ---------------------------------------------------------------------------

def agent_start_event(
    session_id: str, task_id: str, agent_name: str, task_content: str,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.AGENT_START,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        label=f"Agent start: {agent_name}",
        input={"task": task_content},
        surface_op=APPEND,
    )


def agent_call_event(
    session_id: str, task_id: str, agent_name: str,
    step_number: int,
    reasoning: Optional[str] = None,
    assistant_text: Optional[str] = None,
    duration_ms: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
    provider_state: Optional[Dict[str, Any]] = None,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.AGENT_CALL,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        step_number=step_number,
        label=f"Step {step_number}",
        reasoning=reasoning,
        assistant_text=assistant_text,
        message=reasoning,
        success=True,
        duration_ms=duration_ms,
        usage=usage,
        provider_state=provider_state or {},
        # On the surface: this is the assistant's turn. It was log-only while the
        # surface meant "what memory records", which covers results and not the
        # reasoning that produced them — so a compaction could hide a result while
        # leaving the thinking behind it in the history.
        surface_op=APPEND,
    )


def agent_end_event(
    session_id: str, task_id: str, agent_name: str,
    success: bool, result: Optional[str],
    duration_ms: Optional[float] = None, error: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.AGENT_END,
        usage=usage,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        label=f"Agent end: {agent_name} ({'ok' if success else 'fail'})",
        output=result,
        message=str(result) if result is not None else None,
        success=success,
        error=error,
        duration_ms=duration_ms,
        metadata={"success": success},
        surface_op=APPEND,
    )


def tool_start_event(
    session_id: str, task_id: str, agent_name: str,
    step_number: int, action_index: int, action_name: str,
    action_args: Dict[str, Any], call_id: str = "",
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.TOOL_START,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        step_number=step_number, action_index=action_index,
        action_type="tool", action_name=action_name,
        label=f"tool: {action_name}", input=action_args,
        metadata={"call_id": call_id} if call_id else {},
    )


def tool_call_event(
    session_id: str, task_id: str, agent_name: str,
    step_number: int, action_index: int, action_name: str,
    result: Any, success: bool,
    duration_ms: Optional[float] = None, error: Optional[str] = None,
    description: Optional[str] = None, call_id: str = "",
) -> TraceEvent:
    meta: Dict[str, Any] = {"success": success}
    if description:
        meta["description"] = description
    if call_id:
        # The model's own id for the call this result answers. Pairing on
        # (step, index) works only while both events survive; an id survives
        # reordering, replay, and a log read out of context.
        meta["call_id"] = call_id
    return TraceEvent(
        event_type=TraceEventType.TOOL_CALL,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        step_number=step_number, action_index=action_index,
        action_type="tool", action_name=action_name,
        label=f"{action_name} ({'ok' if success else 'fail'})",
        output=result,
        message=str(result) if result is not None else None,
        success=success,
        error=error,
        duration_ms=duration_ms,
        metadata=meta,
        surface_op=APPEND,
    )


def skill_start_event(
    session_id: str, task_id: str, agent_name: str,
    step_number: int, action_index: int, action_name: str,
    action_args: Dict[str, Any], call_id: str = "",
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.SKILL_START,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        step_number=step_number, action_index=action_index,
        action_type="skill", action_name=action_name,
        label=f"skill: {action_name}", input=action_args,
        metadata={"call_id": call_id} if call_id else {},
    )


def skill_call_event(
    session_id: str, task_id: str, agent_name: str,
    step_number: int, action_index: int, action_name: str,
    result: Any, success: bool,
    duration_ms: Optional[float] = None, error: Optional[str] = None,
    description: Optional[str] = None, call_id: str = "",
) -> TraceEvent:
    meta: Dict[str, Any] = {"success": success}
    if description:
        meta["description"] = description
    if call_id:
        # The model's own id for the call this result answers. Pairing on
        # (step, index) works only while both events survive; an id survives
        # reordering, replay, and a log read out of context.
        meta["call_id"] = call_id
    return TraceEvent(
        event_type=TraceEventType.SKILL_CALL,
        session_id=session_id, task_id=task_id, agent_name=agent_name,
        step_number=step_number, action_index=action_index,
        action_type="skill", action_name=action_name,
        label=f"{action_name} ({'ok' if success else 'fail'})",
        output=result,
        message=str(result) if result is not None else None,
        success=success,
        error=error,
        duration_ms=duration_ms,
        metadata=meta,
        surface_op=APPEND,
    )
