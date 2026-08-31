"""Runtime types: AgentRef, status enum, and message classes.

The runtime is a thin mailbox/pump layer on top of existing Agent instances.
- AgentRef is the only externally-visible handle to a running agent.
- Messages (TaskMessage / StopMessage) flow into ref._inbox; the pump loop
  in agentevolver.runtime.pump dispatches them to the underlying Agent._run method.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from agentevolver.utils import make_id


class AgentStatus(str, Enum):
    RUNNING  = "running"
    STOPPING = "stopping"
    STOPPED  = "stopped"
    DEAD     = "dead"


class AgentDeadError(RuntimeError):
    """Raised when sending to an AgentRef that is not RUNNING."""


class BaseMessage(BaseModel):
    """Base class for all runtime messages."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=make_id)
    reply_future: Optional[asyncio.Future] = Field(default=None, exclude=True)


class TaskMessage(BaseMessage):
    """Run one task on the agent (maps to Agent._run(task=..., **kwargs))."""

    task: Optional[str] = None
    kwargs: Dict[str, Any] = Field(default_factory=dict)


class StopMessage(BaseMessage):
    """Graceful stop request: pump exits after this message."""

    reason: str = "manual"


class AgentRef(BaseModel):
    """Handle to one running agent inside the runtime.

    A delegated child is not a different kind of thing — it is one of these that some
    other agent started and is keeping track of. The delegation fields below are empty
    on a ref nobody delegated, and describe the relationship rather than the agent:
    which job it is collected under, whose session may talk to it, how many turns it has
    taken. There is no second registry and no second type for a "sub-agent"; every actor
    is an ``Agent``, and being someone's child is a property of the running instance.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name:       str
    agent_name: str
    status:     AgentStatus = AgentStatus.RUNNING

    #: Set only on a delegated ref. ``job_id`` is the id the parent's model is given —
    #: `job__list` / `job__output` / `job__kill` all take the same string.
    job_id:            str  = ""
    task:              str  = ""
    parent_session_id: str  = ""
    #: Root conversation/task-tree identity used for sibling and descendant messaging.
    root_session_id:   str  = ""
    #: Gateway project identity used only for human visibility/authorization.
    project_id:        str  = ""
    #: The child's OWN session, stable across turns so a continuable child keeps one memory.
    session_id:        str  = ""
    continuable:       bool = False
    turns:             int  = 0
    #: Session-scoped topics this live ref consumes. Subscription is a relationship of
    #: a running ref, not a second kind of Agent, so lifecycle and addressability remain
    #: in the one runtime registry.
    subscriptions:     Set[str] = Field(default_factory=set)
    #: Standing instructions and attachments prepended to every published event turn.
    #: A subscription-only child does not spend a model turn "waiting"; its first turn
    #: begins when an event arrives.
    subscription_brief: str = ""
    subscription_files: List[str] = Field(default_factory=list)
    #: Mid-turn right now. Deliberately not the job's status and not ``AgentStatus``: a
    #: continuable child that finished a turn is idle, not finished — it still holds its
    #: context and can be sent more work — so collapsing the two would report either a
    #: live child as collectable or a finished one as still running.
    busy:              bool = False
    #: Last control state accepted by the protocol. Kept on the ref so a human-facing
    #: Agent view can distinguish an intentionally paused worker from an idle one.
    paused:            bool = False

    _inbox:         asyncio.Queue            = PrivateAttr(default_factory=asyncio.Queue)
    _pump_task:     Optional[asyncio.Task]   = PrivateAttr(default=None)
    _pending_reply: Optional[asyncio.Future] = PrivateAttr(default=None)

    #: Turns waiting to be run, in the order they were sent.
    #:
    #: ``_inbox`` cannot serve as this queue. Delivering two tasks into it back to back
    #: starts a second run on the same ref while the first is still going — ``on_start``
    #: overwrites the run keyed under that ref name — and the first turn's result is then
    #: lost with no error anywhere. Queueing here is what makes "your message becomes its
    #: next turn" true rather than aspirational.
    _tasks:  asyncio.Queue          = PrivateAttr(default_factory=asyncio.Queue)
    #: The one coroutine allowed to run a turn on this ref.
    _driver: Optional[asyncio.Task] = PrivateAttr(default=None)
    _ctx:    Optional[Any]          = PrivateAttr(default=None)

    @property
    def alive(self) -> bool:
        return self.status == AgentStatus.RUNNING

    def label(self) -> str:
        """The job listing's summary — who it is, what it is doing, what it was asked.

        What it is doing is in here because ``job__list`` is where a parent looks to
        recall what it started, and "running" alone cannot separate a child mid-turn from
        one idling with an answer already waiting to be read.
        """
        if not self.alive:
            doing = "gone"
        elif self.paused:
            doing = f"paused (turn {self.turns + 1})"
        elif self.busy:
            doing = f"working (turn {self.turns + 1})"
        else:
            doing = f"idle after {self.turns} turn{'s' if self.turns != 1 else ''}"
        return f"{self.agent_name} · {doing} · {self.task[:60]}"

    def __repr__(self) -> str:
        return f"AgentRef(name={self.name!r}, agent={self.agent_name!r}, status={self.status.value})"

    __str__ = __repr__


AgentRef.model_rebuild()
