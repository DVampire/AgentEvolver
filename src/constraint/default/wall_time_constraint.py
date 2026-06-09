"""WallTimeConstraint — limits total wall-clock time per task."""

from datetime import datetime
from typing import Any, Dict

from pydantic import Field

from src.constraint.types import Constraint, ConstraintContext
from src.response.types import Response, ResponseType
from src.registry import CONSTRAINT


@CONSTRAINT.register_module()
class WallTimeConstraint(Constraint):
    """Stops the agent when elapsed wall-clock time since the first step exceeds the cap."""

    name: str = Field(default="wall_time_constraint")
    description: str = Field(default="Stops the agent when elapsed wall-clock time since the first step exceeds the cap.")
    max_second: float = Field(default=300.0, description="Maximum wall-clock seconds allowed.")

    async def __call__(self, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        if ctx.id not in self._state:
            self._state[ctx.id] = {"start_time": datetime.now()}
        elapsed = (datetime.now() - self._state[ctx.id]["start_time"]).total_seconds()

        data = {
            "elapsed_sec": round(elapsed, 2),
            "max_second": self.max_second
        }
        
        if elapsed >= self.max_second:
            return Response(
                type=ResponseType.CONSTRAINT,
                success=False,
                message=f"Wall-time limit reached ({elapsed:.0f}s/{self.max_second:.0f}s)",
                data=data,
            )
        return Response(type=ResponseType.CONSTRAINT, 
                        success=True, 
                        message=f"Current elapsed [{elapsed:.0f}s/{self.max_second:.0f}s] within limit.", 
                        data=data
                        )
