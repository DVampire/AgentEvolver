"""What a past run looks like to something reading it back.

Everything here is a *view* of the trace log, never a second copy of it. The log is
the record; these models are what one bounded read of it returns. That is why each
one carries the coordinates needed to ask for more — an owner, a session id, a
``seq_no`` — rather than trying to be complete on its own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SessionRecord(BaseModel):
    """One past run, summarised from its trace file.

    A run, not a session directory. One directory under ``output/<owner>/sessions``
    usually holds several of these — the run it was opened for, plus one per
    sub-agent it spawned — and each has its own trace session id. Collapsing them
    into the directory would hide exactly the sub-agent run whose work an agent is
    most often looking for.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(description="Trace session id — the name of its JSONL file.")
    owner: str = Field(description="Owner whose tree the run is filed under.")
    project: str = Field(description="Session directory holding it; the run and its sub-agent runs share one.")
    path: str = Field(description="Absolute path to the JSONL log. For diagnostics; readers go through the query server.")

    event_count: int = Field(default=0, description="Events in the log, including log-only ones.")
    started_at: str = Field(default="", description="Timestamp of the first event, ISO-8601.")
    ended_at: str = Field(default="", description="Timestamp of the last event, ISO-8601.")

    agent_names: List[str] = Field(default_factory=list, description="Agents that emitted events, in first-seen order.")
    task_ids: List[str] = Field(default_factory=list, description="Task ids seen in the log, in first-seen order.")

    task: str = Field(default="", description="What the run was asked to do, from the first agent_start.")
    outcome: str = Field(default="", description="What it answered, from the last agent_end.")
    #: ``None`` when the log has no ``agent_end`` — a run killed mid-flight, or one
    #: still going. Distinct from ``False``: "we never found out" and "it failed" lead
    #: an agent to different next moves, and one bool cannot say both.
    success: Optional[bool] = Field(default=None, description="Verdict of the last agent_end; None when the run has no end event.")

    unreadable_lines: int = Field(default=0, description="Lines that did not parse as an event. Reported, never silently dropped.")

    def summary(self) -> str:
        """One line, for a listing."""
        verdict = "?" if self.success is None else ("ok" if self.success else "fail")
        agents = ",".join(self.agent_names) or "-"
        return (f"{self.session_id}  [{verdict}] {self.event_count:>4} events  "
                f"{self.started_at[:19] or '-'}  {agents}")


class EventHit(BaseModel):
    """One event that matched a search, with just enough to decide whether to read it."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(description="Run the event belongs to.")
    owner: str = Field(description="Owner whose tree the run is filed under.")
    seq_no: int = Field(description="Position in that run's log — what session_event_read_tool takes.")
    event_type: str = Field(description="Event type, e.g. 'tool_call'.")
    agent_name: str = Field(default="", description="Agent that emitted it.")
    action_name: str = Field(default="", description="Tool or skill involved, when there is one.")
    timestamp: str = Field(default="", description="When it happened, ISO-8601.")
    excerpt: str = Field(default="", description="Text around the match, bounded. Never the whole event.")
    terms_matched: int = Field(default=0, description="How many query terms this event carried; the rank.")


class SessionHit(BaseModel):
    """One run that matched a search, plus the event that matched it best."""

    model_config = ConfigDict(extra="forbid")

    record: SessionRecord
    matches: int = Field(default=0, description="Events in the run that matched every term.")
    best: Optional[EventHit] = Field(default=None, description="Highest-ranked matching event; where to start reading.")


class SearchPage(BaseModel):
    """A bounded answer, honest about what it left out.

    ``truncated`` and ``scanned`` exist so the agent can tell "this is all there is"
    from "this is the first twenty of many". Without that it reads a capped list as a
    complete one and concludes the work it is looking for was never done.
    """

    model_config = ConfigDict(extra="forbid")

    sessions: List[SessionHit] = Field(default_factory=list)
    events: List[EventHit] = Field(default_factory=list)
    scanned: int = Field(default=0, description="Runs actually read while answering.")
    truncated: bool = Field(default=False, description="True when the cap stopped the search before the corpus ran out.")


class SessionOutline(BaseModel):
    """One page of a run, one line per event.

    Entries are :class:`EventHit` with nothing matched. They carry exactly the same
    coordinates a search hit carries — session, seq, type, excerpt — so an agent
    reading an outline and an agent reading search results reach the next tool call
    the same way, instead of learning two vocabularies for one idea.
    """

    model_config = ConfigDict(extra="forbid")

    record: SessionRecord
    entries: List["EventHit"] = Field(default_factory=list)
    total: int = Field(default=0, description="Events available under the current reading, across all pages.")
    start: int = Field(default=0, description="Index this page began at.")
    #: True when the page is the history as it stood at the end — a compaction summary
    #: standing in for what it replaced. False means the raw append order: every event
    #: as written, summaries and shadowed originals alike.
    surface_only: bool = Field(default=True, description="Whether the page is the folded surface or the raw write order.")
    surface_error: str = Field(default="", description="Why the surface could not be folded, when it could not. Empty otherwise.")


class EventWindow(BaseModel):
    """One event read exactly, with its neighbours and its links to other events."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner: str
    target: Dict[str, Any] = Field(description="The event as it was written, whole.")
    before: List[Dict[str, Any]] = Field(default_factory=list, description="Preceding events in write order.")
    after: List[Dict[str, Any]] = Field(default_factory=list, description="Following events in write order.")

    #: Seqs this event shadowed, when it is a compaction summary. The whole point of
    #: recording a replacement instead of deleting: from a summary there is a way back
    #: to what it summarised, and it is right here rather than reconstructed by hand.
    shadowed: List[int] = Field(default_factory=list, description="Surface entries this event replaced.")
    shadowed_by: Optional[int] = Field(default=None, description="Seq of the later event that replaced this one, when one did.")
    derived_from: List[int] = Field(default_factory=list, description="Seqs the event cites as its sources.")
    paired_with: Optional[int] = Field(default=None, description="The matching start/result event for a tool or skill call.")


__all__ = [
    "EventHit",
    "EventWindow",
    "SearchPage",
    "SessionHit",
    "SessionOutline",
    "SessionRecord",
]
