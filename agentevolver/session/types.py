from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

from agentevolver.utils import make_id

class BaseContext(BaseModel):
    """Base class for all module-level contexts."""
    id: str = Field(description="Unique execution identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this context.")
    workspace_root: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload for this context.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        workspace_root: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> "BaseContext":
        """Factory: creates a context with a unique ID."""
        return cls(
            id=make_id(),
            name=name,
            workspace_root=workspace_root,
            extra=extra if extra is not None else {}
        )

    @classmethod
    def from_context(cls, ctx: Optional["BaseContext"] = None) -> "BaseContext":
        if ctx is None:
            return cls(id=make_id(),
                       name=None,
                       workspace_root=None,
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
        # escalate_tool / the escalation channel to find the parent).
        extra = dict(getattr(ctx, "extra", {}) or {})
        for k in ("parent_session_id", "subtask_id"):
            v = getattr(ctx, k, None)
            if v is not None:
                extra.setdefault(k, v)
        return cls(
            id=ctx.id,
            name=name,
            workspace_root=getattr(ctx, "workspace_root", None),
            input=getattr(ctx, "input", {}),
            extra=extra,
        )


class SessionContext(BaseContext):
    """Top-level session identifier passed between all managers."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Unique session identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this session.")
    workspace_root: Optional[str] = Field(default=None, description="Working directory for this session.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this session.")
