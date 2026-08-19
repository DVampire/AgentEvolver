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

from typing import Any, Callable, Dict, List, Optional

from agentevolver.capability import MOUNTED_TYPES, mounted_type as capability_type_entry
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

#: Capability types whose members can be judged from a ``permission_mode``
#: declaration. A type absent here — an agent dispatch, a workflow — is refused
#: outright: its effects are whatever the thing it runs does, and that is not
#: knowable from a declaration.
#:
#: Read off :data:`MOUNTED_TYPES` rather than listed again, because a list here
#: is a second answer to a question that table already answers — and the way a new
#: capability type quietly arrives judgeable-by-omission or refused-by-omission
#: depending on which of the two someone remembered to edit.
_JUDGEABLE_TYPES = frozenset(entry.type for entry in MOUNTED_TYPES if entry.judgeable)


def action_is_allowed(capability_type: str, name: str,
                      declaration: Optional[Dict[str, Any]]) -> bool:
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
    if capability_type not in _JUDGEABLE_TYPES or not declaration:
        return False
    if declaration.get("mutates") is False:
        return True
    return declaration.get("permission_mode") == "read_only"


async def declaration_of(capability_type: str, name: str) -> Optional[Dict[str, Any]]:
    """What a capability says about its own effects, or ``None`` if nothing can be read.

    ``None`` covers a type with no registry to ask, a name that is not registered,
    and a manager that has not been initialized. All three mean the same thing to
    the gate — nothing was declared — so they are not distinguished here.
    """
    entry = capability_type_entry(capability_type)
    if entry is None or not entry.judgeable:
        return None
    try:
        info = await entry.manager().get_info(name)
    except Exception as exc:  # noqa: BLE001 — an unreadable declaration is no declaration
        logger.debug(f"| Plan gate could not read {capability_type} {name!r}: {exc}")
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
        #: Called with each new state. The gateway subscribes so a UI can follow the
        #: gate; anything else that needs to know can too.
        self._listeners: List[Callable[[PlanState], None]] = []

    def _listener_list(self) -> List[Callable[[PlanState], None]]:
        """The subscriber list, created on demand.

        `Singleton` hands back an instance built before this field existed, so `__init__`
        does not run again for it and a plain attribute would be missing on exactly the
        long-lived instance everything shares.
        """
        listeners = getattr(self, "_listeners", None)
        if listeners is None:
            listeners = []
            self._listeners = listeners
        return listeners

    def subscribe(self, listener: Callable[[PlanState], None]) -> None:
        """Hear about every plan-state change, whoever made it."""
        self._listener_list().append(listener)

    def unsubscribe(self, listener: Callable[[PlanState], None]) -> None:
        listeners = self._listener_list()
        if listener in listeners:
            listeners.remove(listener)

    def _announce(self, state: PlanState) -> None:
        """Tell every listener the gate moved.

        Announcing here rather than at the call sites is the point. Only the gateway's
        own `plan.set` used to publish, so a plan the *agent* got approved through
        `exit_plan_mode` opened the gate silently — the person had just approved it and
        the bar still read "plan mode on". A state that changes without saying so is a
        UI that lies, and the way to stop that recurring is to make the transition
        responsible for it instead of each caller remembering.

        A listener that raises must not stop the transition: the gate has already moved,
        and refusing to finish because a subscriber failed would leave the caller
        believing it had not.
        """
        for listener in tuple(self._listener_list()):
            try:
                listener(state)
            except Exception as error:                              # noqa: BLE001
                logger.warning(f"| ⚠️ Plan listener failed: {error}")

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
        self._announce(state)
        return state

    def approve(self, session_id: str, plan: str) -> PlanState:
        """Open the gate, recording the plan a person agreed to."""
        state = self.state(session_id)
        state.active = False
        state.approved_plan = plan
        state.approved_at = _now()
        self._states[session_id] = state
        logger.info(f"| 📋 Plan approved for {session_id} ({len(plan)} chars)")
        self._announce(state)
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
        self._announce(state)
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
