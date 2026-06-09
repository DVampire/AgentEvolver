"""Constraint types — Constraint base class and ConstraintContext."""

from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.session import BaseContext
from src.response.types import Response, ResponseType


class ConstraintContext(BaseContext):
    """Context passed into constraint manager and individual constraint instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Task ID — matches the task_id used throughout the agent loop.")
    name: Optional[str] = Field(default=None, description="Name of the agent being constrained.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload for this constraint check.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this constraint context.")


class Constraint(BaseModel):
    """Base class for all constraints.

    Subclass and override ``__call__(input, ctx)`` to implement constraint logic.
    Return ``Response(success=False, ...)`` when violated, ``success=True`` when passing.

    Per-task state lives in ``_state[task_id]`` — a plain dict lazily initialized
    on first use inside ``__call__``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Unique name for this constraint.")
    enabled: bool = Field(default=True, description="Set to False to temporarily disable without removing.")

    _state: Dict[str, Dict[str, Any]] = PrivateAttr(default_factory=dict)

    def _cleanup(self, task_id: str) -> None:
        self._state.pop(task_id, None)

    async def __call__(self, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        return Response(type=ResponseType.CONSTRAINT, success=True, message="")


__all__ = ["ConstraintContext", "Constraint"]
