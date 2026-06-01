"""WallTimeConstraint — limits total wall-clock time per task."""

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext, ConstraintResult
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class WallTimeConstraint(Constraint):
    """Stops the agent when elapsed wall-clock time since the first step exceeds the cap.

    The timer starts when ``constraint_manager.start_session`` is called,
    which happens automatically on the first ``_think_and_act`` call for a task.
    """

    name: str = Field(default="wall_time_constraint")
    max_seconds: float = Field(default=300.0, description="Maximum wall-clock seconds allowed.")

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.elapsed_sec >= self.max_seconds:
            return ConstraintResult(
                violated=True,
                constraint_name=self.name,
                reason=f"Wall-time limit reached ({ctx.elapsed_sec:.0f}s/{self.max_seconds:.0f}s)",
            )
        return ConstraintResult(violated=False)
