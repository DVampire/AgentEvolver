"""Session contexts and bounded views returned when reading historical runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.utils import make_id


class BaseContext(BaseModel):
    """Base class for all module-level contexts."""
    id: str = Field(description="Unique execution identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this context.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload for this context.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")
    # A normal run still inherits the globally-bound session workspace. A delegated
    # worker may carry a narrower, internally-created workspace in ``extra`` (for
    # example a Git worktree), allowing isolated workers to run concurrently without
    # racing on process-global configuration.

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> "BaseContext":
        """Factory: creates a context with a unique ID."""
        return cls(
            id=make_id(),
            name=name,
            extra=extra if extra is not None else {}
        )

    @classmethod
    def from_context(cls, ctx: Optional["BaseContext"] = None) -> "BaseContext":
        if ctx is None:
            return cls(id=make_id(),
                       name=None,
                       input={},
                       extra={})
        # Fall back to the target class's field default when the source value is
        # None — subclasses may narrow Optional fields (e.g. ToolContext.name: str).
        name = getattr(ctx, "name", None)
        if name is None:
            name = cls.model_fields["name"].default
        # Preserve lineage fields that subclasses add (AgentContext.parent_session_id /
        # subtask_id) through the conversion, via ``extra`` — so a converted context
        # (e.g. a tool's ToolContext) can still tell who dispatched it (needed by
        # escalate_tool / the escalation channel to find the parent). ``extra`` also
        # carries the ambient ``sandbox`` handle, so it propagates to sub-agents/tools.
        extra = dict(getattr(ctx, "extra", {}) or {})
        for k in ("parent_session_id", "subtask_id"):
            v = getattr(ctx, k, None)
            if v is not None:
                extra.setdefault(k, v)
        return cls(
            id=ctx.id,
            name=name,
            input=getattr(ctx, "input", {}),
            extra=extra,
        )


class SessionContext(BaseContext):
    """Top-level session identifier passed between all managers."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Unique session identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this session.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this session.")


@dataclass
class SessionUpload:
    """One file being uploaded into a Session's owner-scoped asset store."""

    id: str
    name: str
    path: str
    size: int
    mime_type: str = "application/octet-stream"
    received: int = 0
    completed: bool = False

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "mime_type": self.mime_type,
            "completed": self.completed,
        }


@dataclass
class Session:
    """Live Session state shared by the Gateway and Session context layer."""

    context: SessionContext
    created_at: str
    sandbox: ProjectSandbox
    owner: str = "local"
    updated_at: str = ""
    has_work: bool = False
    task_ids: List[str] = field(default_factory=list)
    capabilities: Dict[str, List[str]] = field(default_factory=dict)
    uploads: Dict[str, SessionUpload] = field(default_factory=dict)


class SessionRecord(BaseModel):
    """One past run summarised from its authoritative Trace log."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(description="Trace session id — the name of its JSONL file.")
    owner: str = Field(description="Owner whose tree the run is filed under.")
    project: str = Field(description="Session directory shared by the run and its delegates.")
    path: str = Field(description="Absolute diagnostic path; readers use the Session server.")
    event_count: int = Field(default=0, description="Events in the log, including log-only ones.")
    started_at: str = Field(default="", description="Timestamp of the first event, ISO-8601.")
    ended_at: str = Field(default="", description="Timestamp of the last event, ISO-8601.")
    agent_names: List[str] = Field(default_factory=list)
    task_ids: List[str] = Field(default_factory=list)
    task: str = Field(default="", description="Task from the first agent_start event.")
    outcome: str = Field(default="", description="Answer from the last agent_end event.")
    success: Optional[bool] = Field(
        default=None,
        description="Verdict of the last agent_end; None when the run has no end event.",
    )
    unreadable_lines: int = Field(
        default=0,
        description="Lines that did not parse as events; never silently dropped.",
    )

    def summary(self) -> str:
        verdict = "?" if self.success is None else ("ok" if self.success else "fail")
        agents = ",".join(self.agent_names) or "-"
        return (
            f"{self.session_id}  [{verdict}] {self.event_count:>4} events  "
            f"{self.started_at[:19] or '-'}  {agents}"
        )


class EventHit(BaseModel):
    """One bounded event search result with coordinates for an exact read."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner: str
    seq_no: int
    event_type: str
    agent_name: str = ""
    action_name: str = ""
    timestamp: str = ""
    excerpt: str = ""
    terms_matched: int = 0


class SessionHit(BaseModel):
    """One matching run and its best matching event."""

    model_config = ConfigDict(extra="forbid")

    record: SessionRecord
    matches: int = 0
    best: Optional[EventHit] = None


class SearchPage(BaseModel):
    """One bounded search response that reports whether work was omitted."""

    model_config = ConfigDict(extra="forbid")

    sessions: List[SessionHit] = Field(default_factory=list)
    events: List[EventHit] = Field(default_factory=list)
    scanned: int = 0
    truncated: bool = False


class SessionOutline(BaseModel):
    """One bounded page of a run with one compact entry per event."""

    model_config = ConfigDict(extra="forbid")

    record: SessionRecord
    entries: List[EventHit] = Field(default_factory=list)
    total: int = 0
    start: int = 0
    surface_only: bool = True
    surface_error: str = ""


class EventWindow(BaseModel):
    """One exact event with neighbours and links to related events."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner: str
    target: Dict[str, Any]
    before: List[Dict[str, Any]] = Field(default_factory=list)
    after: List[Dict[str, Any]] = Field(default_factory=list)
    shadowed: List[int] = Field(default_factory=list)
    shadowed_by: Optional[int] = None
    derived_from: List[int] = Field(default_factory=list)
    paired_with: Optional[int] = None


def resolve_workspace_root(ctx: Any = None, fallback: str = "") -> str:
    """Resolve the effective workspace, preferring an internally-bound child root."""
    extra = getattr(ctx, "extra", None) or {}
    scoped = extra.get("execution_cwd")
    if scoped:
        return str(scoped)
    try:
        from agentevolver.config import config

        configured = str(getattr(config, "workspace_root", "") or "")
        if configured:
            return configured
    except Exception:
        pass
    return str(fallback or "")


def isolated_workspace_root(ctx: Any = None) -> str:
    """Return only a dispatcher-minted isolated cwd, never the global fallback."""
    return str((getattr(ctx, "extra", None) or {}).get("execution_cwd") or "")
