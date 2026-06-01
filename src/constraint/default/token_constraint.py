"""TokenConstraint — limits cumulative LLM token consumption per task."""

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext, ConstraintResult
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class TokenConstraint(Constraint):
    """Stops the agent when total LLM tokens consumed in this task exceeds the cap.

    Token counts are sourced from ``Response.usage.total`` after each
    model call and accumulated in the constraint_manager per task_id.
    """

    name: str = Field(default="token_constraint")
    max_tokens: int = Field(default=10000000, description="Maximum cumulative tokens allowed.") # 10 million by default, which is very high and unlikely to be hit in practice, but can be adjusted as needed.

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.tokens_used >= self.max_tokens:
            return ConstraintResult(
                violated=True,
                constraint_name=self.name,
                reason=f"Token limit reached ({ctx.tokens_used:,}/{self.max_tokens:,})",
            )
        return ConstraintResult(violated=False)
