"""ConstraintManagerServer — thin facade over ConstraintContextManager."""

from __future__ import annotations

from typing import Any, Dict

from src.constraint.context import ConstraintContextManager
from src.constraint.types import Constraint, ConstraintContext
from src.response.types import Response


class ConstraintManagerServer:
    """Global constraint manager — register constraints, call by name."""

    def __init__(self) -> None:
        self.constraint_context_manager: ConstraintContextManager = ConstraintContextManager()

    def register(self, constraint: Constraint) -> None:
        self.constraint_context_manager.register(constraint)

    def cleanup(self, task_id: str) -> None:
        self.constraint_context_manager.cleanup(task_id)

    async def __call__(self, name: str, input: Dict[str, Any], ctx: ConstraintContext) -> Response:
        return await self.constraint_context_manager(name, input, ctx)


# Global singleton
constraint_manager = ConstraintManagerServer()
