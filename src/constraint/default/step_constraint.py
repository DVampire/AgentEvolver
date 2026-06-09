"""StepConstraint — limits the number of steps an agent may take."""

from typing import Any, Dict

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext
from src.response.types import Response, ResponseType
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class StepConstraint(Constraint):
    """Hard cap on the number of think-and-act steps."""

    name: str = Field(default="step_constraint")
    description: str = Field(default="Hard cap on the number of think-and-act steps an agent may take.")
    max_step: int = Field(default=30, description="Maximum number of steps allowed.")

    async def __call__(self, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        if ctx.id not in self._state:
            self._state[ctx.id] = {"step": 0}
        self._state[ctx.id]["step"] += 1

        data = {
            "step": self._state[ctx.id]["step"],
            "max_step": self.max_step
        }

        if self._state[ctx.id]["step"] > self.max_step:
            return Response(
                type=ResponseType.CONSTRAINT,
                success=False,
                message=f"Step limit reached ({self._state[ctx.id]['step']}/{self.max_step})",
                data=data,
            )
        return Response(type=ResponseType.CONSTRAINT, 
                        success=True,
                        message=f"Current step is [{self._state[ctx.id]['step']}/{self.max_step}] within limit.", 
                        data=data
                        )
