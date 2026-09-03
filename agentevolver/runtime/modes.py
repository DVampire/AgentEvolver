"""Interaction modes — how work reaches a process, and what the kernel does about it.

A leaf module with nothing but the standard library, so both the kernel below and the
agent layer above can name a mode without either importing the other.

## Why a name at all

"Subscriber" was not a thing this runtime knew. It was `resident=True` plus
`topics=[...]` plus `start_idle=True` plus a context of its own plus a standing brief
that leads every turn — five facts, assembled by hand at each call site, and assembling
them wrong raised nothing. Three defects came from exactly that:

- subscribers spawned with the parent's context shared one browser tab, and each read a
  page another had just navigated away from;
- a standing brief was dropped on one delivery path, so a participant answered
  "NO ASSIGNED CONTEXT" about a persona it had been given;
- a topic registered unscoped while the publisher looked up a scoped name, so every
  fan-out reached nobody and reported success.

None of those are hard to get right once. They are hard to get right at every call site,
forever. A mode is one place that knows what a subscriber is.

## Why these three names

Networking separates three questions that are easy to conflate:

- **Topology** — the shape of the whole system: star, bus, tree, mesh. Describes a
  system, not a participant, so it belongs in a design document rather than in a field.
- **Message exchange pattern** — the shape of one interaction: request/reply,
  publish/subscribe, one-way.
- **Endpoint role** — what a single endpoint does.

ZeroMQ's decision is the one worth borrowing: the name goes on the ENDPOINT, not on the
conversation. There is no `PUBSUB` socket; a `PUB` and a `SUB` together make pub/sub.
Naming the conversation leaves a participant ambiguous — an agent labelled "broadcast"
is either the publisher or one of the listeners, and the runtime cannot tell which.

So these are endpoint roles, and they map onto patterns that predate us:

    RESPONDER   REP              answer once, exit
    SERVICE     ROUTER/DEALER    resident, addressed by pid
    SUBSCRIBER  SUB              resident, addressed by topic

## What this mode is NOT

It is the *inbound* half only: how work arrives. Whether a process may start others is a
property of the agent template rather than of its lifecycle, and lives there as
`include_agents` — the difference between a leaf and an orchestrator.

The two are independent, and one process routinely holds several roles at once, exactly
as one ZeroMQ process holds several sockets. The website builder answers a task
(RESPONDER), dispatches sub-agents (orchestrator), and publishes releases (PUB).

One pattern is deliberately absent: PUSH/PULL, where N workers compete for one queue.
Dispatch here names its worker; nothing hands work to whoever is free.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, NamedTuple


class InteractionMode(str, Enum):
    """How a process receives work. The inbound half of a participant's role."""

    #: Answers one request and exits. The first envelope IS the task, and the final
    #: report goes to whoever spawned it. Request/reply, from the replying side.
    RESPONDER = "responder"

    #: Resident, and addressed by pid. Runs its first task immediately, then parks IDLE
    #: for the next one. Whoever holds the pid can keep the conversation going; nobody
    #: else can reach it. Asynchronous request/reply.
    SERVICE = "service"

    #: Resident, and addressed by topic. Does NOT run on spawn — what it waits for has
    #: not happened yet, and running early would mean acting on nothing. Its `task` is a
    #: standing brief, not a first job, and it leads every turn thereafter. The publisher
    #: does not know it exists, which is the point of the indirection.
    SUBSCRIBER = "subscriber"


class Lifecycle(NamedTuple):
    """What a mode means to the kernel, in the kernel's own terms."""

    #: Park IDLE after a turn instead of exiting.
    resident: bool
    #: Register IDLE and wait, rather than running the first input immediately.
    start_idle: bool
    #: Whether a topic edge is required, forbidden, or neither.
    topics: str  # "required" | "forbidden"


LIFECYCLES: Dict[InteractionMode, Lifecycle] = {
    InteractionMode.RESPONDER: Lifecycle(
        resident=False, start_idle=False, topics="forbidden",
    ),
    InteractionMode.SERVICE: Lifecycle(
        resident=True, start_idle=False, topics="forbidden",
    ),
    InteractionMode.SUBSCRIBER: Lifecycle(
        resident=True, start_idle=True, topics="required",
    ),
}


def lifecycle(mode: InteractionMode) -> Lifecycle:
    """The kernel-facing meaning of one mode."""
    try:
        return LIFECYCLES[InteractionMode(mode)]
    except (KeyError, ValueError) as error:
        known = ", ".join(item.value for item in InteractionMode)
        raise ValueError(f"unknown interaction mode {mode!r}; one of: {known}") from error


def check_topics(mode: InteractionMode, topics: object) -> None:
    """Refuse a mode and a topic list that contradict each other.

    A SUBSCRIBER without a topic waits for an event nobody can send it, and a RESPONDER
    with one registers an edge it will drop when it exits moments later. Both look like
    a working spawn and neither does anything, so both are refused where the mistake is
    made rather than discovered from a run that produced no output.
    """
    requirement = lifecycle(mode).topics
    has_topics = bool(topics)
    if requirement == "required" and not has_topics:
        raise ValueError(
            f"mode {InteractionMode(mode).value!r} is addressed by topic and needs at "
            "least one; a subscriber with no topic waits for an event nobody can send"
        )
    if requirement == "forbidden" and has_topics:
        raise ValueError(
            f"mode {InteractionMode(mode).value!r} is addressed by pid, so topics do "
            "nothing; use mode 'subscriber' to be addressed by topic"
        )


def infer(resident: bool, topics: object) -> InteractionMode:
    """The mode a legacy flag combination meant.

    Call sites assembled these booleans by hand before there was a mode to name, and
    both spellings stay accepted for now: this maps the old one onto the new so there is
    exactly one implementation of what each combination does.
    """
    if topics:
        return InteractionMode.SUBSCRIBER
    return InteractionMode.SERVICE if resident else InteractionMode.RESPONDER


__all__ = [
    "LIFECYCLES",
    "InteractionMode",
    "Lifecycle",
    "check_topics",
    "infer",
    "lifecycle",
]
