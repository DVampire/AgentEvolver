"""TokenConstraint — limits cumulative LLM token consumption per task."""

from typing import Any, Dict

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext
from src.response.types import Response, ResponseType
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class TokenConstraint(Constraint):
    """Stops the agent when total LLM tokens consumed in this task exceeds the cap."""

    name: str = Field(default="token_constraint")
    description: str = Field(default="Stops the agent when total LLM tokens consumed in this task exceeds the cap.")
    max_token: int = Field(default=10_000_000, description="Maximum cumulative tokens allowed.")

    async def __call__(self, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        if ctx.id not in self._state:
            self._state[ctx.id] = {"token": 0}
        self._state[ctx.id]["token"] += input.get("token", 0)
        token = self._state[ctx.id]["token"]

        data = {
            "token": token,
            "max_token": self.max_token
        }
        if token >= self.max_token:
            return Response(
                type=ResponseType.CONSTRAINT,
                success=False,
                message=f"Token limit reached ({token:,}/{self.max_token:,})",
                data=data,
            )
        return Response(type=ResponseType.CONSTRAINT, 
                        success=True,
                        message=f"Current token [{token:,}/{self.max_token:,}] within limit.", 
                        data=data
                        )
