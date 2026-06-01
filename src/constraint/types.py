"""Constraint types — Constraint base class, ConstraintContext, ConstraintResult."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConstraintContext(BaseModel):
    """Runtime snapshot passed to each Constraint.check() call."""

    task_id: str = Field(description="Task ID — matches the task_id used throughout the agent loop.")
    agent_name: str = Field(description="Name of the agent being constrained.")
    step_number: int = Field(default=0, description="Current step index (0-based).")
    tokens_used: int = Field(default=0, description="Cumulative LLM tokens consumed so far in this task.")
    elapsed_sec: float = Field(default=0.0, description="Seconds elapsed since the first step of this task.")


class ConstraintResult(BaseModel):
    """Result returned by Constraint.check()."""

    violated: bool = Field(default=False)
    constraint_name: str = Field(default="")
    reason: str = Field(default="")


class Constraint(BaseModel):
    """Base class for all constraints.

    Subclass and override ``check`` to implement a new constraint dimension.
    Attach instances to an Agent via ``constraints=[...]``; an empty list
    means no constraints are active.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Unique name for this constraint.")
    enabled: bool = Field(default=True, description="Set to False to temporarily disable without removing.")

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        """Return a ConstraintResult indicating whether this constraint is violated.

        The default implementation always returns not-violated; subclasses must override.
        """
        return ConstraintResult(violated=False)


__all__ = ["ConstraintContext", "ConstraintResult", "Constraint"]
