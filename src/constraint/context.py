"""ConstraintContextManager — constraint enforcement."""

from __future__ import annotations

from typing import Any, Dict

from src.constraint.types import Constraint, ConstraintContext
from src.response.types import Response, ResponseType
from src.logger import logger

class ConstraintContextManager:
    """Registry of constraints, callable by name. Per-task state lives inside each constraint."""

    def __init__(self) -> None:
        self._registry: Dict[str, Constraint] = {}

    def register(self, constraint: Constraint) -> None:
        self._registry[constraint.name] = constraint

    def cleanup(self, task_id: str) -> None:
        for c in self._registry.values():
            c._cleanup(task_id)

    async def __call__(self, name: str, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        constraint = self._registry.get(name)
        if not constraint or not constraint.enabled:
            return Response(type=ResponseType.CONSTRAINT, success=True, message="")
        result = await constraint(input, ctx)
        if not result.success:
            logger.warning(f"| 🚫 Constraint violated [{name}] task_id={ctx.id}: {result.message}")
        return result
