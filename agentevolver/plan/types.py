"""What plan mode is, for one run.

Deliberately three facts and no history: whether the gate is closed, what plan was
last put up for review, and whether a person approved it. A log of every entry and
exit would be a second, weaker copy of the trace — the trace already records the
`exit_plan_mode` call and its review — and nothing reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanState(BaseModel):
    """Where one run stands with respect to plan mode."""

    session_id: str = Field(default="", description="The run this state belongs to.")
    #: The gate. While true, the hook refuses any action not declared free of effects.
    active: bool = Field(default=False, description="Whether the agent is held to planning only.")
    #: The plan the person approved, kept after the gate opens. Without it, an approval
    #: is a bare boolean and nothing downstream can say what was agreed to — which is
    #: the one fact worth having when the work afterwards goes somewhere else.
    approved_plan: str = Field(default="", description="The plan a person approved, verbatim.")
    entered_at: Optional[str] = Field(default=None)
    approved_at: Optional[str] = Field(default=None)

    def summary(self) -> Dict[str, Any]:
        """The state as a UI receives it."""
        return {
            "session_id": self.session_id,
            "active": self.active,
            "approved_plan": self.approved_plan,
            "entered_at": self.entered_at,
            "approved_at": self.approved_at,
        }


__all__ = ["PlanState"]
