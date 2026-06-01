"""StepConstraint — limits the number of steps an agent may take."""

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext, ConstraintResult
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class StepConstraint(Constraint):
    """Hard cap on the number of think-and-act steps.

    Supersedes the per-agent ``max_steps`` parameter when used in
    ``constraints=[StepConstraint(max_steps=N)]``.
    """

    name: str = Field(default="step_constraint")
    max_steps: int = Field(default=30, description="Maximum number of steps allowed.")

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.step_number >= self.max_steps:
            return ConstraintResult(
                violated=True,
                constraint_name=self.name,
                reason=f"Step limit reached ({ctx.step_number}/{self.max_steps})",
            )
        return ConstraintResult(violated=False)
