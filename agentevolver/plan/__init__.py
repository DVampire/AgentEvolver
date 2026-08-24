"""How much planning a run is held to, and the document that holds the plan.

Three stances. `off` asks for nothing. `auto` — the default — leaves it to the agent:
it is told to keep `plan.md` current for anything past a single obvious step, and the
document is rendered back to it every step. `plan` is the gate: a person approves the
approach before anything changes, and their approval is what writes `plan.md`.
"""

from .server import (
    ALWAYS_ALLOWED,
    AUTO_MODE_NOTICE,
    PLAN_MODE_NOTICE,
    PlanManagerServer,
    action_is_allowed,
    declaration_of,
    plan_manager,
    plan_path,
    read_plan,
    write_plan,
)
from .types import PlanMode, PlanState

__all__ = [
    "ALWAYS_ALLOWED",
    "AUTO_MODE_NOTICE",
    "PLAN_MODE_NOTICE",
    "PlanManagerServer",
    "PlanMode",
    "PlanState",
    "action_is_allowed",
    "declaration_of",
    "plan_manager",
    "plan_path",
    "read_plan",
    "write_plan",
]
