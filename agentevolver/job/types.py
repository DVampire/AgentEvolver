"""What a background unit of work is.

A job is deliberately thin: something was started, it is producing output, and it will
end. The kind of work is not modelled — a shell command, a PTY send, and a sub-agent are
all the same three questions ("is it done", "what did it say", "stop it"), and giving each
its own type would give the agent three sets of tools for one concept.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """Where a job is.

    ``KILLED`` is separate from ``FAILED`` because the difference matters to the agent:
    one is the command's own verdict on the work, the other is a decision the agent
    itself made, and collapsing them would let a job it stopped read as a job that broke.
    """

    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    KILLED = "killed"

    @property
    def is_final(self) -> bool:
        return self is not JobStatus.RUNNING


class Job(BaseModel):
    """One background unit of work, as the registry holds it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(description="Short handle the agent uses to name this job.")
    kind: str = Field(description="What started it — 'bash', 'terminal', 'agent'. Descriptive only; the controller treats every kind alike.")
    label: str = Field(default="", description="Human-readable summary, e.g. the command line. Shown in listings so a job can be recognised without reading its output.")
    session_id: str = Field(default="", description="The session that started it. Jobs are session-local; this is what scopes a listing.")
    status: JobStatus = Field(default=JobStatus.RUNNING)
    exit_code: Optional[int] = Field(default=None, description="None while running, and for a job killed before it could exit.")
    error: Optional[str] = Field(default=None, description="Why it failed to start or run, when that is not expressible as an exit code.")
    started_at: float = Field(default_factory=time.monotonic)
    ended_at: Optional[float] = Field(default=None)

    #: Output accumulated so far. Held whole rather than streamed away, because reading
    #: must not consume: an agent that checks early and checks again has to see the
    #: earlier output still there, or it cannot tell "nothing new" from "I missed it".
    output: str = Field(default="")
    #: Set when output passed the cap. The output is truncated at the head, not the tail —
    #: a command's last lines are almost always the ones that say what happened.
    truncated: bool = Field(default=False)

    #: The live thing behind the job (a Popen, a task). Not serialized; the registry uses
    #: it to signal and reap, and nothing outside should reach for it.
    handle: Optional[Any] = Field(default=None, exclude=True, repr=False)

    @property
    def elapsed(self) -> float:
        return (self.ended_at or time.monotonic()) - self.started_at

    def summary(self) -> str:
        """One line, for a listing.

        Elapsed time is included for running jobs because it is the only signal that
        separates "working" from "hung", and an agent with no way to tell them apart
        waits forever on either.
        """
        state = self.status.value
        if self.status is JobStatus.EXITED and self.exit_code is not None:
            state = f"exited({self.exit_code})"
        elif self.status is JobStatus.FAILED and self.error:
            state = f"failed: {self.error[:60]}"
        return f"{self.id}  {state:<22} {self.elapsed:6.1f}s  {self.kind}: {self.label[:70]}"


__all__ = ["Job", "JobStatus"]
