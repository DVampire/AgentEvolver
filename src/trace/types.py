"""TraceEvent type hierarchy — every agent execution step produces one of these."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.utils import make_id


class TraceEventType(str, Enum):
    """Granular execution event types, mirroring the agent lifecycle."""

    # Agent lifecycle
    AGENT_START   = "agent_start"    # Agent.__call__ begins
    AGENT_END     = "agent_end"      # Agent.__call__ ends (success or failure)

    # Step lifecycle (one iteration of the while loop)
    STEP_START    = "step_start"
    STEP_END      = "step_end"

    # LLM calls
    LLM_START     = "llm_start"      # model_manager called
    LLM_END       = "llm_end"        # model_manager returned

    # Actions (tool / skill / text) — each action in the actions list
    ACTION_START  = "action_start"   # about to execute an action
    ACTION_END    = "action_end"     # action result received

    # Convenience aliases surfaced in UI
    TOOL_CALL     = "tool_call"      # action_type == "tool"
    TOOL_RESULT   = "tool_result"
    SKILL_CALL    = "skill_call"     # action_type == "skill"
    SKILL_RESULT  = "skill_result"
    PLAN_TEXT     = "plan_text"      # action_type == "text"

    # Plan lifecycle
    PLAN_INIT     = "plan_init"   # step 0: agent sets its execution plan
    PLAN_UPDATE   = "plan_update" # step N: agent updates plan item statuses

    # Misc
    ERROR         = "error"
    CUSTOM        = "custom"


class EventProvenance(str, Enum):
    """Where this event originated."""
    LIVE      = "live"       # real agent execution
    TEST      = "test"       # test/simulation run
    REPLAY    = "replay"     # replayed from persisted trace
    HEALTHCHECK = "healthcheck"  # health probe after compaction


class EventConfidence(str, Enum):
    """Confidence level — gates automated downstream actions."""
    HIGH   = "high"     # safe to act on automatically
    MEDIUM = "medium"   # human review recommended before acting
    LOW    = "low"      # diagnostic only, do not auto-act


class TraceEvent(BaseModel):
    """A single, immutable trace record produced during agent execution."""

    id: str = Field(
        default_factory=lambda: make_id(),
        description="Globally unique event ID.",
    )
    event_type: TraceEventType

    # Hierarchy identifiers — all optional so partial context is still valid
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_name: Optional[str] = None

    # Human-readable label shown in the UI tree node
    label: str = Field(default="", description="Short display label.")

    # Step / action position within the agent run
    step_number: Optional[int] = None
    action_index: Optional[int] = None
    action_type: Optional[str] = None   # "tool" | "skill" | "text"
    action_name: Optional[str] = None

    # Payload — input and output as freeform dicts for display
    input: Optional[Dict[str, Any]] = Field(default=None)
    output: Optional[Any] = Field(default=None)
    error: Optional[str] = None

    # Performance
    duration_ms: Optional[float] = None

    # Extra structured data (token counts, model name, etc.)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Event ordering and deduplication (lane_events.rs pattern)
    seq_no: Optional[int] = Field(default=None, description="Monotonic per-session sequence number.")
    fingerprint: Optional[str] = Field(default=None, description="SHA256(event_type+session_id+step+action) for dedup.")
    provenance: EventProvenance = Field(default=EventProvenance.LIVE)
    confidence: EventConfidence = Field(default=EventConfidence.HIGH)

    def to_dict(self) -> Dict[str, Any]:
        """Return JSON-serialisable dict."""
        d = self.model_dump()
        d["timestamp"] = self.timestamp.isoformat()
        return d


def compute_event_fingerprint(event: TraceEvent) -> str:
    """SHA256-based deduplication fingerprint for a trace event."""
    import hashlib
    parts = [
        event.event_type.value,
        event.session_id or "",
        str(event.step_number or ""),
        str(event.action_index or ""),
        event.action_name or "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Helpers for constructing common events
# ---------------------------------------------------------------------------

def agent_start_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    task_content: str,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.AGENT_START,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        label=f"Agent start: {agent_name}",
        input={"task": task_content},
    )


def agent_end_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    success: bool,
    result: Optional[str],
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.AGENT_END,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        label=f"Agent end: {agent_name} ({'ok' if success else 'fail'})",
        output=result,
        error=error,
        duration_ms=duration_ms,
        metadata={"success": success},
    )


def step_start_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.STEP_START,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        label=f"Step {step_number}",
    )


def step_end_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
    thinking: Optional[str] = None,
    next_goal: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.STEP_END,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        label=f"Step {step_number} done",
        output={"thinking": thinking, "next_goal": next_goal},
        duration_ms=duration_ms,
    )


def llm_start_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
    model: str,
    message_count: int,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.LLM_START,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        label=f"LLM call ({model})",
        metadata={"model": model, "message_count": message_count},
    )


def llm_end_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
    model: str,
    duration_ms: float,
    actions_count: int,
) -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.LLM_END,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        label=f"LLM done ({actions_count} actions)",
        duration_ms=duration_ms,
        metadata={"model": model, "actions_count": actions_count},
    )


def action_start_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
    action_index: int,
    action_type: str,
    action_name: str,
    action_args: Dict[str, Any],
) -> TraceEvent:
    type_map = {
        "tool": TraceEventType.TOOL_CALL,
        "skill": TraceEventType.SKILL_CALL,
        "text": TraceEventType.PLAN_TEXT,
    }
    ev_type = type_map.get(action_type, TraceEventType.ACTION_START)
    return TraceEvent(
        event_type=ev_type,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        action_index=action_index,
        action_type=action_type,
        action_name=action_name,
        label=f"{action_type}: {action_name}",
        input=action_args,
    )


def action_end_event(
    session_id: str,
    task_id: str,
    agent_name: str,
    step_number: int,
    action_index: int,
    action_type: str,
    action_name: str,
    result: Any,
    success: bool,
    duration_ms: float,
    error: Optional[str] = None,
) -> TraceEvent:
    type_map = {
        "tool": TraceEventType.TOOL_RESULT,
        "skill": TraceEventType.SKILL_RESULT,
    }
    ev_type = type_map.get(action_type, TraceEventType.ACTION_END)
    return TraceEvent(
        event_type=ev_type,
        session_id=session_id,
        task_id=task_id,
        agent_name=agent_name,
        step_number=step_number,
        action_index=action_index,
        action_type=action_type,
        action_name=action_name,
        label=f"{action_name} result ({'ok' if success else 'fail'})",
        output=result,
        error=error,
        duration_ms=duration_ms,
        metadata={"success": success},
    )
