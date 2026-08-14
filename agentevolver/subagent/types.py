"""What a delegated child agent is once its parent stops waiting for it.

A foreground delegation needs no record: the call blocks, the result comes back, and the
child is over before the parent takes its next decision. A background one needs all of
this, because the parent will ask about it later — which child was that, is it still
mine to talk to, how many turns has it taken, where does what it said accumulate.

The field that decides everything else is ``continuable``. A one-shot child answers once
and ends; a continuable child stays alive between turns and can be given more work on the
same conversation. Both are backgrounded the same way and read back the same way; the
difference is only whether the child is still there afterwards.

Turn state is deliberately *not* the job's status. A continuable child that finished a
turn is idle, not finished — it still holds its context and can be sent more work — so
collapsing the two would either report a live child as collectable or a finished one as
still running. The Job says whether the child exists; ``state`` says what it is doing.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChildState(str, Enum):
    """What a background child is doing right now.

    ``IDLE`` only ever describes a continuable child. A one-shot child goes straight
    from ``WORKING`` to ``GONE``, and a state it can never be in is worth not offering:
    a parent that sees "idle" knows a ``send_message_tool`` will be taken.
    """

    WORKING = "working"
    IDLE = "idle"
    GONE = "gone"


class Subagent(BaseModel):
    """One background child agent, as its parent's registry holds it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    #: The child *is* a job — this is its id in the job registry, and the only handle
    #: the model is ever given for it. `job_list_tool` / `job_output_tool` /
    #: `job_kill_tool` take the same string.
    job_id: str
    agent_name: str = Field(description="Which registered agent is running as this child.")
    task: str = Field(default="", description="The brief it was started on, for listings.")
    parent_session_id: str = Field(default="", description="The session that started it; what scopes a listing and what reaps it.")
    session_id: str = Field(default="", description="The child's OWN session id, stable across turns so a continuable child keeps one memory.")
    continuable: bool = Field(default=False, description="Whether it stays alive between turns and can be sent more work.")
    turns: int = Field(default=0, description="Turns completed. A continuable child's second turn continues the first's conversation.")
    state: ChildState = Field(default=ChildState.WORKING)

    #: The live things behind the child. Not serialized, and nothing outside the
    #: manager should reach for them: the ref is how messages get in, the driver is
    #: the one coroutine allowed to run a turn on it.
    ref: Optional[Any] = Field(default=None, exclude=True, repr=False)
    ctx: Optional[Any] = Field(default=None, exclude=True, repr=False)
    driver: Optional[Any] = Field(default=None, exclude=True, repr=False)

    #: Turns waiting to be run, in the order they were sent.
    #:
    #: The child's own inbox cannot serve as this queue. Delivering two tasks into it
    #: back to back starts a second run on the same ref while the first is still
    #: going — ``on_start`` overwrites the run it keyed under that ref name — and the
    #: first turn's result is then lost with no error anywhere. Queueing here is what
    #: makes "your message becomes its next turn" true rather than aspirational.
    inbox: Any = Field(default_factory=asyncio.Queue, exclude=True, repr=False)

    @property
    def alive(self) -> bool:
        return self.state is not ChildState.GONE

    def label(self) -> str:
        """The listing line's summary — who it is, what it is doing, what it was asked.

        The state is in here because ``job_list_tool`` is where a parent looks to
        recall what it started, and "running" alone cannot separate a child mid-turn
        from one idling with an answer already waiting to be read.
        """
        if self.state is ChildState.IDLE:
            doing = f"idle after {self.turns} turn{'s' if self.turns != 1 else ''}"
        elif self.state is ChildState.GONE:
            doing = "gone"
        else:
            doing = f"working (turn {self.turns + 1})"
        return f"{self.agent_name} · {doing} · {self.task[:60]}"

    def handoff(self) -> str:
        """What the parent is told at the moment it backgrounds this child.

        Names the follow-up calls explicitly. A parent handed only an id has to
        already know that a sub-agent is a job, and one that does not know goes back
        to blocking delegations — the capability then exists and is never used.
        """
        lines = [
            f"Started {self.agent_name} in the background as {self.job_id}. "
            f"It does not block you; keep working.",
            f'  job_output_tool(job_id="{self.job_id}")  — what it has reported and returned',
            f"  job_list_tool()                      — every job and its state",
            f'  job_kill_tool(job_id="{self.job_id}")    — stop it',
        ]
        if self.continuable:
            lines.insert(1, f'  send_message_tool(job_id="{self.job_id}", message=...) — give it more '
                            f"work on the same conversation")
            lines.append("It stays alive between turns, so collect its result before you finish.")
        else:
            lines.append("It answers once and ends. Collect the result before you finish; "
                         "an uncollected sub-agent is work you paid for and threw away.")
        return "\n".join(lines)


__all__ = ["ChildState", "Subagent"]
