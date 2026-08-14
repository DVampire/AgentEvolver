"""PlanManagerServer — who is in plan mode, and what an action in plan mode may do.

Two halves that are deliberately kept apart.

The **state** half is a per-run flag. It is small enough to be obvious and is held
in memory, session-local, like the job registry: plan mode is a stance a person
takes toward a run in progress, and it means nothing once that run is over.

The **decision** half, :func:`action_is_allowed`, is a pure function over what a
capability *declared* about itself. It never looks at the capability's name.
Classifying by name is how the predecessor of ``repeat_tool.py`` went wrong — a name
is not a behaviour, and a gate acting on a mislabel acts irreversibly. So the rule
here reads two declared fields and nothing else, and anything that declares neither
is refused rather than guessed at: in a gate, the unknown case is the one that has
to be safe.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agentevolver.logger import logger
from agentevolver.plan.types import PlanState, _now
from agentevolver.utils import Singleton

#: What the model is told when the gate turns an action away. Carries the way out,
#: because a refusal that does not say how to stop being refused produces an agent
#: that retries the same call until its budget is gone.
PLAN_MODE_NOTICE = (
    "You are in plan mode: a person has asked to approve your approach before you "
    "change anything. Keep reading, searching and reasoning — those are unaffected — "
    "and when you know what you intend to do, call `exit_plan_mode` with the complete "
    "plan. Nothing that changes state runs until they approve it."
)

#: Capabilities the gate never turns away, whatever they declare. Each is either the
#: way out of plan mode, the way to talk to the person holding it, or the way to end
#: a run — and blocking any of those leaves the agent with no legal move at all.
ALWAYS_ALLOWED = frozenset({"exit_plan_mode", "ask_user_question", "done_tool"})

#: Kinds whose members can be judged from a ``permission_mode`` declaration. A kind
#: absent here — an agent dispatch, a workflow — is refused outright: its effects are
#: whatever the thing it runs does, and that is not knowable from a declaration.
_JUDGEABLE_KINDS = frozenset({"tool", "skill", "connector", "environment"})


def action_is_allowed(kind: str, name: str, declaration: Optional[Dict[str, Any]]) -> bool:
    """Whether an action may run while plan mode is active.

    Allowed when the capability declared it does not mutate (``mutates is False``)
    or that it is ``read_only``. Both are the capability's own claim about itself,
    written next to the code that knows.

    Refused otherwise, including when there is no declaration to read. A tool with
    no ``mutates`` field has not said it is safe, it has said nothing — and
    ``bash_tool`` is exactly that tool. Treating silence as permission would let the
    one capability that can do anything through the gate that exists to hold it.
    """
    if name in ALWAYS_ALLOWED:
        return True
    if kind not in _JUDGEABLE_KINDS or not declaration:
        return False
    if declaration.get("mutates") is False:
        return True
    return declaration.get("permission_mode") == "read_only"


async def declaration_of(kind: str, name: str) -> Optional[Dict[str, Any]]:
    """What a capability says about its own effects, or ``None`` if nothing can be read.

    ``None`` covers a kind with no registry to ask, a name that is not registered,
    and a manager that has not been initialized. All three mean the same thing to
    the gate — nothing was declared — so they are not distinguished here.
    """
    if kind not in _JUDGEABLE_KINDS:
        return None
    managers = {
        "tool": "agentevolver.tool.server",
        "skill": "agentevolver.skill.server",
        "connector": "agentevolver.connector.server",
        "environment": "agentevolver.environment.server",
    }
    try:
        import importlib

        module = importlib.import_module(managers[kind])
        manager = getattr(module, f"{kind}_manager")
        info = await manager.get_info(name)
    except Exception as exc:  # noqa: BLE001 — an unreadable declaration is no declaration
        logger.debug(f"| Plan gate could not read {kind} {name!r}: {exc}")
        return None
    if info is None:
        return None
    return {
        "mutates": getattr(info, "mutates", None),
        "permission_mode": getattr(info, "permission_mode", None),
    }


class PlanManagerServer(metaclass=Singleton):
    """Holds which runs are in plan mode, and what was approved to leave it."""

    def __init__(self) -> None:
        self._states: Dict[str, PlanState] = {}

    def state(self, session_id: str) -> PlanState:
        """This run's plan state. A run nobody put in plan mode is not in it."""
        return self._states.get(session_id) or PlanState(session_id=session_id)

    def active(self, session_id: str) -> bool:
        return self.state(session_id).active

    def enter(self, session_id: str) -> PlanState:
        """Close the gate on a run.

        Any previous approval is cleared. Re-entering plan mode is a person saying
        they want to approve the *next* thing, and carrying the last plan forward
        would let a second round of work inherit consent given for the first.
        """
        state = PlanState(session_id=session_id, active=True, entered_at=_now())
        self._states[session_id] = state
        logger.info(f"| 📋 Plan mode on for {session_id}")
        return state

    def approve(self, session_id: str, plan: str) -> PlanState:
        """Open the gate, recording the plan a person agreed to."""
        state = self.state(session_id)
        state.active = False
        state.approved_plan = plan
        state.approved_at = _now()
        self._states[session_id] = state
        logger.info(f"| 📋 Plan approved for {session_id} ({len(plan)} chars)")
        return state

    def leave(self, session_id: str) -> PlanState:
        """Open the gate without an approval — the person called it off.

        Distinct from :meth:`approve` because ``approved_plan`` stays empty: nothing
        downstream should be able to read a cancelled plan mode as an agreed plan.
        """
        state = self.state(session_id)
        state.active = False
        self._states[session_id] = state
        logger.info(f"| 📋 Plan mode off for {session_id}")
        return state

    def forget(self, session_id: str) -> None:
        self._states.pop(session_id, None)


plan_manager = PlanManagerServer()

__all__ = [
    "PlanManagerServer",
    "plan_manager",
    "action_is_allowed",
    "declaration_of",
    "PLAN_MODE_NOTICE",
    "ALWAYS_ALLOWED",
]
