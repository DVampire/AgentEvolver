from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.utils import make_id

class BaseContext(BaseModel):
    """Base class for all module-level contexts."""
    id: str = Field(description="Task/execution ID.")
    name: Optional[str] = Field(default=None, description="Optional name for this context.")
    timeout: Optional[float] = Field(default=3600, description="Optional timeout for this context. Defaults to 1 hour.")
    work_dir: Optional[str] = Field(default=None, description="Optional working directory for this context.")
    input: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        timeout: Optional[float] = 3600,
        work_dir: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "BaseContext":
        """Factory: creates a context with a unique ID."""
        return cls(
            id=make_id(),
            name=name,
            timeout=timeout,
            work_dir=work_dir,
            meta=meta if meta is not None else {}
        )

    @classmethod
    def from_context(cls, ctx: Optional["BaseContext"] = None) -> "BaseContext":
        if ctx is None:
            return cls(id=make_id(),
                       name=None,
                       timeout=3600,
                       work_dir=None,
                       input={},
                       meta={})
        return cls(
            id=ctx.id,
            name=getattr(ctx, "name", None),
            timeout=getattr(ctx, "timeout", 3600),
            work_dir=getattr(ctx, "work_dir", None),
            input=getattr(ctx, "input", {}),
            meta=getattr(ctx, "meta", {}),
        )


class SessionContext(BaseContext):
    """Top-level session identifier passed between all managers."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Session ID.")
    name: Optional[str] = Field(default=None, description="Optional name for this session.")
    timeout: Optional[float] = Field(default=3600, description="Optional timeout for this session. Defaults to 1 hour.")
    work_dir: Optional[str] = Field(default=None, description="Optional working directory for this session.")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Optional meta metadata for this session.")
