from typing import Any, Dict
from pydantic import BaseModel, Field
from src.utils import generate_unique_id


class SessionContext(BaseModel):
    """Top-level session identifier passed between all managers."""
    id: str = Field(
        default_factory=lambda: generate_unique_id("session"),
        description="Unique identifier for this session.",
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary transient data any module can attach to the session.",
    )


class BaseContext(BaseModel):
    """Base class for all module-level contexts.

    id is always copied from the originating SessionContext so every context
    in the same session shares the same id and can be correlated.
    """
    id: str = Field(description="Session id, copied from SessionContext.")

    @classmethod
    def from_session(cls, ctx: "SessionContext", **kwargs) -> "BaseContext":
        """Construct this context from a SessionContext, copying the id."""
        return cls(id=ctx.id, **kwargs)
