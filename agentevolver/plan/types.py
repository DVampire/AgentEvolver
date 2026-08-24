"""What plan mode is, for one run.

Three facts and no history: which mode the run is in, whether the gate is currently
shut, and what plan was last approved. A log of every entry and exit would be a
second, weaker copy of the trace — the trace already records the `exit_plan_mode`
call and its review — and nothing reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanMode(str, Enum):
    """How much planning this run is held to.

    Three rather than two because the useful answer is usually neither extreme. A gate
    on every run makes a one-line fix cost a review; no gate at all means the run that
    rewrites forty files does it without anyone seeing the shape first.

    ``AUTO`` is the default. The agent decides whether the task warrants a plan and
    writes ``plan.md`` itself if it does — no gate, so a trivial task costs nothing, and
    the document is in front of both the agent and the person for the whole run.

    A caveat worth stating where the mode is defined: an agent that decides whether to
    be reviewed will often decide not to be. This session's own test run reached for
    `write_file_tool` on its first turn, on a task that said not to plan. ``AUTO`` is
    therefore a prompt-level obligation, not an enforced one — it buys the plan being
    *visible and revisable*, not the plan existing. ``PLAN`` is what buys that, and it
    is one flag away.
    """

    #: No plan is asked for and nothing is gated.
    OFF = "off"
    #: The agent decides. It is told to keep `plan.md` current for anything that is not
    #: a single obvious step, and the document is rendered back to it every step.
    AUTO = "auto"
    #: A person will approve the approach first. Every action that changes anything is
    #: refused until they do, and the approved plan is written to `plan.md`.
    PLAN = "plan"


class PlanState(BaseModel):
    """Where one run stands with respect to plan mode."""

    session_id: str = Field(default="", description="The run this state belongs to.")
    #: Which of the three stances this run is under. Separate from `active` because a
    #: run in `PLAN` whose plan has been approved is still a run that was planned — the
    #: gate is what opened, not the mode.
    mode: PlanMode = Field(default=PlanMode.AUTO,
                           description="off / auto / plan. `auto` is the default.")
    #: The gate. While true, the hook refuses any action not declared free of effects.
    #: Only ever true under `PlanMode.PLAN`.
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
            "mode": self.mode.value,
            "active": self.active,
            "approved_plan": self.approved_plan,
            "entered_at": self.entered_at,
            "approved_at": self.approved_at,
        }


__all__ = ["PlanMode", "PlanState"]
