"""What a background unit of work is.

A job is deliberately thin: something was started, it is producing output, and it will
end. The kind of work is not modelled — a shell command, a PTY send, and a sub-agent are
all the same three questions ("is it done", "what did it say", "stop it"), and giving each
its own type would give the agent three sets of tools for one concept.

A reminder is the same three questions asked about work that has not started yet, so it
is a job too: scheduled instead of running, due instead of finished, cancelled by the
same kill. The alternative — a second registry with its own list and its own delete —
would be a second vocabulary for one idea, and the two would drift.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: Floor on a fixed-rate reminder, in seconds. A reminder that can fire every few
#: seconds is not a reminder, it is a loop the model cannot get out of: each
#: delivery costs a turn, and the turn arrives before the previous one's work is
#: done. Five minutes is the reference implementation's floor and the same
#: reasoning applies here.
MIN_EVERY_SECONDS = 300


class ScheduleError(ValueError):
    """A reminder that cannot be scheduled, with the reason the caller must fix."""


class JobStatus(str, Enum):
    """Where a job is.

    ``KILLED`` is separate from ``FAILED`` because the difference matters to the agent:
    one is the command's own verdict on the work, the other is a decision the agent
    itself made, and collapsing them would let a job it stopped read as a job that broke.

    ``SCHEDULED`` is work that has not begun. It is not final — the record still has a
    future — which is why ``is_final`` names the two live states rather than excluding
    ``RUNNING`` alone: written the other way, every reminder would be born finished and
    the eviction pass would drop it before it ever fired.
    """

    SCHEDULED = "scheduled"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    KILLED = "killed"

    @property
    def is_final(self) -> bool:
        return self not in (JobStatus.RUNNING, JobStatus.SCHEDULED)


class Job(BaseModel):
    """One background unit of work, as the registry holds it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(description="Short handle the agent uses to name this job.")
    type: str = Field(description="What started it — 'bash', 'terminal', 'agent'. Descriptive only; the controller treats every type alike.")
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

    #: When this reminder next comes due, in epoch seconds. ``None`` for ordinary work.
    #:
    #: Wall clock, not the monotonic clock ``started_at`` uses, because a reminder can
    #: name an absolute instant and the monotonic clock has no opinion about instants.
    #: Everything that compares against it must use the same clock.
    due_at: Optional[float] = Field(default=None)
    #: Fixed interval for a repeating reminder; ``None`` for a one-shot.
    every_seconds: Optional[int] = Field(default=None)
    #: How many times this reminder has come due and been collected.
    deliveries: int = Field(default=0)

    @property
    def is_reminder(self) -> bool:
        return self.due_at is not None

    @property
    def elapsed(self) -> float:
        return (self.ended_at or time.monotonic()) - self.started_at

    def seconds_until_due(self, now: float) -> Optional[float]:
        """Negative once overdue, so one number answers both "when" and "yet?"."""
        return None if self.due_at is None else self.due_at - now

    def summary(self, now: Optional[float] = None) -> str:
        """One line, for a listing.

        Elapsed time is included for running jobs because it is the only signal that
        separates "working" from "hung", and an agent with no way to tell them apart
        waits forever on either. A scheduled reminder answers the other question — how
        long until it fires — and shows that in the same column, because "0.0s elapsed"
        on something that has not started is worse than no number at all.
        """
        state = self.status.value
        timing = f"{self.elapsed:6.1f}s"
        if self.status is JobStatus.EXITED and self.exit_code is not None:
            state = f"exited({self.exit_code})"
        elif self.status is JobStatus.FAILED and self.error:
            state = f"failed: {self.error[:60]}"
        elif self.status is JobStatus.SCHEDULED and self.due_at is not None:
            remaining = self.due_at - (time.time() if now is None else now)
            state = "scheduled" if remaining > 0 else "DUE NOW"
            if self.every_seconds:
                state += f"/{format_interval(self.every_seconds)}"
            timing = (f"in {format_interval(remaining)}" if remaining > 0
                      else f"{format_interval(-remaining)} late")
        return f"{self.id}  {state:<22} {timing:>10}  {self.type}: {self.label[:70]}"


def format_interval(seconds: float) -> str:
    """A duration a reader can act on: "45s", "12m", "3h10m"."""
    seconds = int(max(0, round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds // 60}m{seconds % 60}s"
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h" if rest // 60 == 0 else f"{hours}h{rest // 60}m"


def parse_absolute(at: str) -> float:
    """One RFC 3339 instant, in epoch seconds.

    An offset is required. "2026-08-15T09:00:00" is not an instant — it is an instant
    per time zone — and guessing the host's zone is how a reminder set from a browser
    in Shanghai fires at breakfast in California.
    """
    text = str(at).strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError(
            f"{at!r} is not an RFC 3339 time. Use 2026-08-15T09:00:00+08:00 or "
            f"2026-08-15T01:00:00Z."
        ) from None
    if moment.tzinfo is None:
        raise ScheduleError(
            f"{at!r} has no UTC offset, so it names no instant. Add one (+08:00) or "
            f"use Z for UTC."
        )
    return moment.timestamp()


def resolve_due(*, now: float, after_seconds: Optional[int] = None,
                at: Optional[str] = None,
                every_seconds: Optional[int] = None) -> Tuple[float, Optional[int]]:
    """Turn one selector into (first due time, repeat interval).

    Exactly one selector. Two would have to be reconciled, and any rule for doing
    that would be invented here rather than asked for by the caller.
    """
    chosen = [name for name, value in
              (("after_seconds", after_seconds), ("at", at), ("every_seconds", every_seconds))
              if value is not None and value != ""]
    if len(chosen) != 1:
        raise ScheduleError(
            "Give exactly one of after_seconds, at, or every_seconds; "
            f"got {chosen or 'none'}."
        )

    if after_seconds is not None and after_seconds != "":
        delay = _positive_int(after_seconds, "after_seconds")
        return now + delay, None

    if at is not None and at != "":
        due = parse_absolute(at)
        if due <= now:
            raise ScheduleError(f"{at!r} is not in the future; it has already passed.")
        return due, None

    interval = _positive_int(every_seconds, "every_seconds")
    if interval < MIN_EVERY_SECONDS:
        raise ScheduleError(
            f"every_seconds must be at least {MIN_EVERY_SECONDS} ({MIN_EVERY_SECONDS // 60} "
            f"minutes); {interval} would interrupt faster than the work can proceed."
        )
    return now + interval, interval


def next_occurrence(*, anchor: float, every_seconds: int, now: float) -> float:
    """The first occurrence after ``now``, still aligned to the creation anchor.

    Alignment is kept rather than restarted from ``now`` so a reminder set on the hour
    stays on the hour. Missed occurrences are skipped, not replayed: an agent that was
    busy for a day must not come back to 288 identical reminders.
    """
    if every_seconds <= 0:
        raise ScheduleError("every_seconds must be positive")
    elapsed = now - anchor
    steps = int(elapsed // every_seconds) + 1
    return anchor + steps * every_seconds


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ScheduleError(f"{name} must be a whole number of seconds; got {value!r}.") from None
    if number <= 0:
        raise ScheduleError(f"{name} must be greater than zero; got {number}.")
    return number


__all__ = [
    "Job",
    "JobStatus",
    "ScheduleError",
    "MIN_EVERY_SECONDS",
    "format_interval",
    "parse_absolute",
    "resolve_due",
    "next_occurrence",
]
