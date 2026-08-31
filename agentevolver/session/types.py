from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

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
