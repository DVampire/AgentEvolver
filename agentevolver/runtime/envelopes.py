"""What travels on a mailbox.

Four kinds, because a process needs to tell them apart to know what to do:

``TaskEnvelope``    work to run — the first one starts the process, later ones are the
                    next turn of a resident process.
``EventEnvelope``   something happened on a topic this process subscribed to.
``ReportEnvelope``  a child speaking to its parent, either mid-run or on exit.
``ReplyEnvelope``   a parent answering a child that is blocked waiting for it.

Action results are deliberately NOT here. A tool's output is the process's own local
data, not a message between processes; routing it through the mailbox is what made the
previous runtime's turn loop span four entry points.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentevolver.utils import make_id


@dataclass(frozen=True)
class Envelope:
    """Common header. Frozen: a delivered message is a record of what was sent."""

    #: Unique id, so a reply can name what it answers.
    id: str = field(default_factory=make_id)
    #: pid of the sender, or "" when the kernel itself posted it.
    sender: str = ""
    #: Wall-clock seconds, for ordering in logs rather than for logic.
    at: float = field(default_factory=time.time)

    @property
    def type(self) -> str:
        """Lower-case type name without the ``Envelope`` suffix."""
        return self.__class__.__name__.removesuffix("Envelope").lower()

    def summary(self) -> str:
        """One short line for logs."""
        return f"{self.type}({self.id[:8]})"


@dataclass(frozen=True)
class TaskEnvelope(Envelope):
    """Work for a process to run: the first turn, or the next one if it is resident."""

    task: str = ""
    files: List[str] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        head = self.task.strip().splitlines()[0] if self.task.strip() else "(empty)"
        return f"task({head[:60]})"


@dataclass(frozen=True)
class EventEnvelope(Envelope):
    """A published event, delivered to every process subscribed to its topic."""

    topic: str = ""
    event_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        return f"event({self.topic}/{self.event_type})"


@dataclass(frozen=True)
class ReportEnvelope(Envelope):
    """A child speaking to its parent.

    ``final`` marks the one the kernel posts when the child exits — the parent's
    equivalent of SIGCHLD, and what lets a dispatching agent park in ``recv()`` instead
    of polling. Non-final reports are progress or a request for guidance.
    """

    text: str = ""
    final: bool = False
    exit_status: Optional[str] = None
    #: Set when the child is blocked waiting for a ReplyEnvelope before it can continue.
    blocked: bool = False

    def summary(self) -> str:
        mark = "final" if self.final else ("blocked" if self.blocked else "progress")
        return f"report[{mark}] from {self.sender[:8]}"


@dataclass(frozen=True)
class ReplyEnvelope(Envelope):
    """A parent answering a blocked child, or any directed message between processes."""

    text: str = ""
    #: Id of the report this answers, when it answers one.
    in_reply_to: str = ""

    def summary(self) -> str:
        return f"reply({self.text.strip()[:60]})"


__all__ = [
    "Envelope",
    "EventEnvelope",
    "ReplyEnvelope",
    "ReportEnvelope",
    "TaskEnvelope",
]
