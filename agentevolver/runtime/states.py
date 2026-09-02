"""Process states, exit statuses, and the transitions the kernel will allow.

A process is in exactly ONE state at a time. That is the whole point of this module:
the previous runtime spread the same question across ``status``, ``busy``, ``paused``,
``continuable`` and a ``_resume_gate`` event, so "is this child collectable?" had five
places to disagree and no single answer.

``IDLE`` is the state those flags could not express. A resident process that finished a
turn is neither running nor finished — it holds its conversation and its memory and
waits for the next message. Collapsing it into either neighbour reports a live child as
collectable, or a finished one as still running.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class ProcessState(str, Enum):
    """Where a process is in its life."""

    #: Registered, not yet handed its first task.
    NEW = "new"
    #: Executing a turn — holding a model call or a tool batch.
    RUNNING = "running"
    #: Alive with no work. Resident processes park here between turns.
    IDLE = "idle"
    #: Held by a signal. Messages may queue but no turn may start.
    SUSPENDED = "suspended"
    #: Winding down at a safe point; the agent gets its landing hook here.
    STOPPING = "stopping"
    #: Finished, carrying an :class:`ExitStatus`.
    EXITED = "exited"


class ExitStatus(str, Enum):
    """Why a process left. Read together with ``Process.last_result``."""

    #: The agent finished its work and said so.
    DONE = "done"
    #: The agent raised, or exhausted its step budget without finishing.
    FAILED = "failed"
    #: A stop or kill signal ended it, or its parent went away.
    CANCELLED = "cancelled"


#: The only moves the kernel will make. Anything else is a bug in the caller, not a
#: state to be silently coerced — see :func:`check_transition`.
TRANSITIONS: Dict[ProcessState, FrozenSet[ProcessState]] = {
    # NEW → IDLE is how a subscriber registers: it exists and is listening, without
    # spending a turn on work that has not arrived yet.
    ProcessState.NEW: frozenset(
        {ProcessState.RUNNING, ProcessState.IDLE, ProcessState.STOPPING}
    ),
    ProcessState.RUNNING: frozenset(
        {ProcessState.IDLE, ProcessState.SUSPENDED, ProcessState.STOPPING}
    ),
    ProcessState.IDLE: frozenset(
        {ProcessState.RUNNING, ProcessState.SUSPENDED, ProcessState.STOPPING}
    ),
    ProcessState.SUSPENDED: frozenset(
        {ProcessState.RUNNING, ProcessState.IDLE, ProcessState.STOPPING}
    ),
    ProcessState.STOPPING: frozenset({ProcessState.EXITED}),
    ProcessState.EXITED: frozenset(),
}

#: States in which a process still exists as far as the process table is concerned.
LIVE: FrozenSet[ProcessState] = frozenset(
    {ProcessState.NEW, ProcessState.RUNNING, ProcessState.IDLE, ProcessState.SUSPENDED}
)

#: States from which a new turn may begin. ``SUSPENDED`` is deliberately absent: that is
#: what suspension means, and it is enforced here rather than at each call site.
SCHEDULABLE: FrozenSet[ProcessState] = frozenset(
    {ProcessState.NEW, ProcessState.RUNNING, ProcessState.IDLE}
)

#: The states a suspended process can be restored to. Recorded when it suspends, so
#: resuming an idle subscriber does not accidentally mark it as running a turn.
RESUMABLE_TO: FrozenSet[ProcessState] = frozenset(
    {ProcessState.RUNNING, ProcessState.IDLE}
)


def can_transition(current: ProcessState, target: ProcessState) -> bool:
    """Whether ``current -> target`` is a move the kernel makes."""
    return target in TRANSITIONS.get(current, frozenset())


def check_transition(current: ProcessState, target: ProcessState) -> None:
    """Raise unless ``current -> target`` is legal.

    Raises:
        InvalidTransition: The move is not in :data:`TRANSITIONS`.
    """
    if not can_transition(current, target):
        from agentevolver.runtime.errors import InvalidTransition

        allowed = sorted(state.value for state in TRANSITIONS.get(current, frozenset()))
        raise InvalidTransition(
            f"cannot move a process from {current.value!r} to {target.value!r}; "
            f"legal moves from {current.value!r} are {allowed or ['(none)']}"
        )


__all__ = [
    "LIVE",
    "RESUMABLE_TO",
    "SCHEDULABLE",
    "TRANSITIONS",
    "ExitStatus",
    "ProcessState",
    "can_transition",
    "check_transition",
]
