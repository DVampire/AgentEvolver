"""ConstraintManagerServer — thin facade over ConstraintContextManager.

Mirrors the pattern of ToolManagerServer / AgentManagerServer.
All logic lives in ConstraintContextManager; this class owns the singleton
instance and exposes a stable public API.
"""

from __future__ import annotations

from typing import List, Optional

from src.constraint.context import ConstraintContextManager
from src.constraint.types import Constraint, ConstraintResult


class ConstraintManagerServer:
    """Global constraint manager — thin facade over ConstraintContextManager."""

    def __init__(self) -> None:
        self.constraint_context_manager: ConstraintContextManager = ConstraintContextManager()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        task_id: str,
        constraints: List[Constraint],
        agent_name: str,
    ) -> None:
        self.constraint_context_manager.start_session(task_id, constraints, agent_name)

    def has_session(self, task_id: str) -> bool:
        return self.constraint_context_manager.has_session(task_id)

    def end_session(self, task_id: str) -> None:
        self.constraint_context_manager.end_session(task_id)

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def record_tokens(self, task_id: str, tokens: int) -> None:
        self.constraint_context_manager.record_tokens(task_id, tokens)

    # ------------------------------------------------------------------
    # Constraint check
    # ------------------------------------------------------------------

    async def check(
        self,
        task_id: str,
        step_number: int,
    ) -> Optional[ConstraintResult]:
        return await self.constraint_context_manager.check(task_id, step_number)


# Global singleton
constraint_manager = ConstraintManagerServer()
