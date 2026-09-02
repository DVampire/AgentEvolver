"""Five read-only tools over the trace logs of runs that already finished.

Without these a self-evolving system cannot learn from itself. Every run writes a full
record of what it tried and what came back, and every run then starts blind: the record
exists on disk and nothing can reach it, so the same dead end is walked again at full
price. These are the way in.

They come as a set of five because retrieval has two granularities and both matter.
Searching finds a *run* (``session_search_tool``) or an *event* (``session_event_search_tool``);
reading takes a run's outline (``session_read_tool``) or one event exactly
(``session_event_read_tool``); and ``session_trace_tool`` says which runs belong
together. A surface with only the coarse half sends the agent a whole log to read one
tool result; with only the fine half it can never find the run in the first place.

Every result is bounded, and says when it was. That is not politeness — an unbounded
search result is how a prompt blows past its limit, and a capped result that does not
admit it is how an agent concludes work was never done.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.session.server import (
    DEFAULT_HITS,
    DEFAULT_OUTLINE,
    MAX_HITS,
    MAX_OUTLINE,
    session_manager,
)
from agentevolver.tool.spill import SpillSource
from agentevolver.tool.spill import save_text as spill_text
from agentevolver.tool.types import Tool

#: An event read inline above this goes to the spill store instead, and the tool
#: returns a preview plus the locator. One event can be an entire build log — the
#: whole reason the result was worth reading is also the reason it cannot be pasted.
INLINE_EVENT_CHARS = 6_000

_SEARCH_DESCRIPTION = "Find past runs whose recorded work matches a description."
_SEARCH_GUIDANCE = """
Search every finished run on this machine for one whose log carries all of your words,
then start from the best-matching event. Use it before starting work that may already
have been done: the task, the tool calls, the results, and the final answer of every
past run are all searchable.

Terms are ANDed and matched anywhere in the run, not all in one event — a description
is normally spread across the task, the work, and the answer.
"""

_SEARCH_EXAMPLES = [
    '{"name": "session_search_tool", "args": {"query": "fibonacci generator"}}',
    '{"name": "session_search_tool", "args": {"query": "pytest failing", "agent_name": "code_agent"}}',
]

_EVENT_SEARCH_DESCRIPTION = "Find individual recorded steps matching a query, in one past run or across all of them."
_EVENT_SEARCH_GUIDANCE = """
Search at step granularity and get back coordinates — a session id and a seq number —
to read exactly. Give a session_id to search inside one run; leave it out to search
every run. Here every term must appear in the *same* event, because the answer is a
place to read from.
"""

_EVENT_SEARCH_EXAMPLES = [
    '{"name": "session_event_search_tool", "args": {"query": "ModuleNotFoundError"}}',
    '{"name": "session_event_search_tool", "args": {"query": "seaborn", "event_type": "tool_call", "action_name": "bash_tool"}}',
]

_READ_DESCRIPTION = "Read a past run step by step, one line per step."
_READ_GUIDANCE = """
Page through what a past run actually did, in order, one line per step. This is the
overview; follow a line that matters with session_event_read_tool to get that step
whole.

By default you see the run's history as it stood at the end — a compaction summary in
place of the steps it replaced, which is what that run's own agent saw. Pass
surface_only=false to see the raw append order instead: every event as written,
summaries and the steps they shadowed alike.
"""

_READ_EXAMPLES = [
    '{"name": "session_read_tool", "args": {"session_id": "code_agent-1edbf044"}}',
    '{"name": "session_read_tool", "args": {"session_id": "code_agent-1edbf044", "start": 40}}',
]

_EVENT_READ_DESCRIPTION = "Read one recorded step of a past run in full, with its neighbours."
_EVENT_READ_GUIDANCE = """
Get one event exactly as it was written — arguments, output, error, timing — plus a few
steps either side for context, and its links to other events: which step a result
answers, and which steps a compaction summary stands in for.

An oversized event is saved to a file and you get a preview plus its path, so a huge
tool result costs you a locator instead of your context.
"""

_EVENT_READ_EXAMPLES = [
    '{"name": "session_event_read_tool", "args": {"session_id": "f49d6082", "seq_no": 5}}',
    '{"name": "session_event_read_tool", "args": {"session_id": "f49d6082", "seq_no": 5, "before": 2, "after": 2}}',
]

_TRACE_DESCRIPTION = "Show which past runs belong together — a run and the sub-agent runs it spawned."
_TRACE_GUIDANCE = """
One run is rarely the whole story: a meta-agent's log records that it delegated, and
the delegate's own log records what was actually done. This lists every run filed under
the same session directory, with each one's task and outcome, so you can move from the
run you found to the one that holds the work.
"""

_TRACE_EXAMPLES = [
    '{"name": "session_trace_tool", "args": {"session_id": "f49d6082"}}',
]


def _session_key(kwargs: Dict[str, Any]) -> str:
    """Which session's spill directory an artifact belongs in."""
    ctx = kwargs.get("ctx")
    return str((getattr(ctx, "extra", {}) or {}).get("project_root") or "")


def _call_id(kwargs: Dict[str, Any]) -> str:
    return str(getattr(kwargs.get("ctx"), "id", "") or "")


def _not_found(session_id: str) -> Response:
    """A named run that is not on disk.

    Reported with what *is* there. A bare "not found" leaves the agent unable to tell a
    typo from an empty corpus, and its next move — guessing another id — is wrong in
    both cases.
    """
    known = [record.session_id for record in session_manager.sessions(limit=10)]
    return Response(
        type=ResponseType.TOOL, success=False,
        message=(f"No past run {session_id!r}. Most recent runs: "
                 f"{', '.join(known) if known else '(none recorded)'}"),
    )


@TOOL.register_module(force=True)
class SessionSearchTool(Tool):
    """Find past runs matching a description."""

    name: str = "session_search_tool"
    description: str = _SEARCH_DESCRIPTION
    guidance: str = _SEARCH_GUIDANCE
    examples: List[str] = _SEARCH_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads finished trace logs; writes nothing.")
    mutates: Optional[bool] = Field(default=False, description="Reads the record of past runs.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, query: str, agent_name: Optional[str] = None,
                       limit: int = DEFAULT_HITS, **kwargs) -> Response:
        """Find past runs by what was worked on.

        Args:
            query: Words describing the work, e.g. "penguins EDA matplotlib".
            agent_name: Only runs where this agent emitted events.
            limit: Hits to return, default 20, capped at 50.
        """
        if not str(query or "").strip():
            return Response(type=ResponseType.TOOL, success=False,
                            message="session_search_tool needs a query — some words describing the work.")

        page = session_manager.search_sessions(query, agent_name=agent_name, limit=limit)
        if not page.sessions:
            return Response(
                type=ResponseType.TOOL, success=True,
                message=(f"No past run matches {query!r} ({page.scanned} run(s) searched). "
                         f"Terms are ANDed — try fewer or more common words."),
                data={"scanned": page.scanned, "sessions": []},
            )

        lines = []
        for hit in page.sessions:
            record = hit.record
            verdict = "?" if record.success is None else ("ok" if record.success else "FAILED")
            lines.append(
                f"{record.session_id}  [{verdict}]  {record.started_at[:19]}  "
                f"{'/'.join(record.agent_names) or '-'}  ({record.event_count} events, "
                f"{hit.matches} matching)\n"
                f"    task: {record.task or '(none recorded)'}\n"
                f"    outcome: {record.outcome or '(no end event)'}"
                + (f"\n    best match at seq {hit.best.seq_no} ({hit.best.event_type}): {hit.best.excerpt}"
                   if hit.best else "")
            )
        header = f"{len(page.sessions)} past run(s) matching {query!r}, newest first"
        if page.truncated:
            header += f" — capped at {MAX_HITS if limit > MAX_HITS else limit}, narrow the query for the rest"
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{header}:\n\n" + "\n\n".join(lines) + (
                "\n\nRead one with session_read_tool, or a single step with session_event_read_tool."),
            data={"scanned": page.scanned, "truncated": page.truncated,
                  "sessions": [hit.model_dump() for hit in page.sessions]},
        )


@TOOL.register_module(force=True)
class SessionEventSearchTool(Tool):
    """Find individual recorded steps, in one past run or across all of them."""

    name: str = "session_event_search_tool"
    description: str = _EVENT_SEARCH_DESCRIPTION
    guidance: str = _EVENT_SEARCH_GUIDANCE
    examples: List[str] = _EVENT_SEARCH_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads finished trace logs; writes nothing.")
    mutates: Optional[bool] = Field(default=False, description="Reads the record of past runs.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, query: str, session_id: Optional[str] = None,
                       event_type: Optional[str] = None, action_name: Optional[str] = None,
                       limit: int = DEFAULT_HITS, **kwargs) -> Response:
        """Find individual events across runs.

        Args:
            query: Words that must all appear in one event.
            session_id: Restrict to one run.
            event_type: e.g. "tool_call", "agent_end", "error".
            action_name: e.g. "bash_tool" — the tool or skill involved.
            limit: Hits to return, default 20, capped at 50.
        """
        if not str(query or "").strip():
            return Response(type=ResponseType.TOOL, success=False,
                            message="session_event_search_tool needs a query — words that appear in the step.")
        if session_id and session_manager.find(session_id) is None:
            return _not_found(session_id)

        page = session_manager.search_events(
            query, session_id=session_id, event_type=event_type,
            action_name=action_name, limit=limit)
        scope = f"run {session_id}" if session_id else f"{page.scanned} run(s)"
        if not page.events:
            return Response(
                type=ResponseType.TOOL, success=True,
                message=(f"No step in {scope} carries all of {query!r}. Every term must "
                         f"appear in the same step here; session_search_tool matches across a whole run."),
                data={"scanned": page.scanned, "events": []},
            )

        lines = [
            f"{hit.session_id}#{hit.seq_no}  {hit.event_type}"
            f"{'/' + hit.action_name if hit.action_name else ''}  {hit.timestamp[:19]}\n"
            f"    {hit.excerpt}"
            for hit in page.events
        ]
        header = f"{len(page.events)} step(s) matching {query!r} in {scope}"
        if page.truncated:
            header += " — capped, narrow the query for the rest"
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{header}:\n\n" + "\n".join(lines) + (
                "\n\nRead one whole with session_event_read_tool using its session id and seq."),
            data={"scanned": page.scanned, "truncated": page.truncated,
                  "events": [hit.model_dump() for hit in page.events]},
        )


@TOOL.register_module(force=True)
class SessionReadTool(Tool):
    """Page through a past run, one line per step."""

    name: str = "session_read_tool"
    description: str = _READ_DESCRIPTION
    guidance: str = _READ_GUIDANCE
    examples: List[str] = _READ_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads finished trace logs; writes nothing.")
    mutates: Optional[bool] = Field(default=False, description="Reads the record of past runs.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, session_id: str, start: int = 0, limit: int = DEFAULT_OUTLINE,
                       surface_only: bool = True, **kwargs) -> Response:
        """Read a run's history as an outline.

        Args:
            session_id: From ``session_search_tool``.
            start: Index within the run to begin at, default 0.
            limit: Lines to return, default 40, capped at 200.
            surface_only: Folded history (default) or raw write order.
        """
        outline = session_manager.outline(session_id, start=max(0, int(start)),
                                        limit=limit, surface_only=surface_only)
        if outline is None:
            return _not_found(session_id)

        record = outline.record
        verdict = "?" if record.success is None else ("ok" if record.success else "FAILED")
        head = [
            f"{record.session_id}  [{verdict}]  {record.event_count} events  "
            f"{record.started_at[:19]} → {record.ended_at[:19]}  "
            f"agents: {'/'.join(record.agent_names) or '-'}",
            f"task: {record.task or '(none recorded)'}",
            f"outcome: {record.outcome or '(no end event)'}",
        ]
        if record.unreadable_lines:
            head.append(f"note: {record.unreadable_lines} line(s) of this log did not parse.")
        if outline.surface_error:
            # Saying which reading you got matters more than the failure itself: an
            # agent told nothing would read shadowed originals as live history.
            head.append(f"note: the surface could not be folded ({outline.surface_error}); "
                        f"showing raw write order instead.")

        body = [
            f"{hit.seq_no:>5}  {hit.event_type:<14} "
            f"{(hit.action_name or hit.agent_name or ''):<18} {hit.excerpt}"
            for hit in outline.entries
        ]
        shown = outline.start + len(outline.entries)
        footer = (f"\n\nShowing {outline.start}–{max(outline.start, shown - 1)} of {outline.total}"
                  f"{'' if shown >= outline.total else f'; call again with start={shown} for more'}"
                  f". {'Folded history' if outline.surface_only else 'Raw write order'}"
                  f"; capped at {MAX_OUTLINE} lines per call.")
        return Response(
            type=ResponseType.TOOL, success=True,
            message="\n".join(head) + "\n\n" + "\n".join(body) + footer,
            data={"record": record.model_dump(), "total": outline.total,
                  "start": outline.start, "next_start": shown if shown < outline.total else None},
        )


@TOOL.register_module(force=True)
class SessionEventReadTool(Tool):
    """Read one recorded step of a past run in full."""

    name: str = "session_event_read_tool"
    description: str = _EVENT_READ_DESCRIPTION
    guidance: str = _EVENT_READ_GUIDANCE
    examples: List[str] = _EVENT_READ_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads finished trace logs; writes nothing.")
    mutates: Optional[bool] = Field(default=False, description="Reads the record of past runs.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, session_id: str, seq_no: int, before: int = 0, after: int = 0,
                       **kwargs) -> Response:
        """Read one step of a run, with optional neighbours.

        Args:
            session_id: From ``session_search_tool``.
            seq_no: The step's position, from a search hit or ``session_read_tool``.
            before: Preceding events to include, default 0, max 10.
            after: Following events to include, default 0, max 10.
        """
        window = session_manager.event_window(session_id, int(seq_no),
                                            before=before, after=after)
        if window is None:
            if session_manager.find(session_id) is None:
                return _not_found(session_id)
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"Run {session_id} has no step {seq_no}. Seq numbers are contiguous "
                         f"from 0; use session_read_tool to see which exist."),
            )

        body = json.dumps(window.target, ensure_ascii=False, indent=2)
        links = []
        if window.paired_with is not None:
            links.append(f"paired with seq {window.paired_with} (the other half of this call)")
        if window.shadowed:
            links.append(f"stands in place of seqs {window.shadowed} — read those for what it summarised")
        if window.derived_from:
            links.append(f"cites seqs {window.derived_from} as its sources")
        if window.shadowed_by is not None:
            links.append(f"later replaced on the surface by seq {window.shadowed_by}")

        if len(body) > INLINE_EVENT_CHARS:
            ref = await spill_text(
                body,
                SpillSource(tool_name=self.name, call_id=_call_id(kwargs), label="event"),
                session_key=_session_key(kwargs),
                suggested_name=f"{session_id}-{seq_no}.json",
            )
            body = (
                f"[Event omitted inline as one complete unit: original_chars={len(body):,}. "
                f"{ref.retrieval_hint}]"
                if ref is not None else
                f"[Event has {len(body):,} characters and could not be saved; narrow with "
                "session_event_search_tool instead.]"
            )

        context = ""
        if window.before or window.after:
            # The target is marked in its own list rather than left to the reader to
            # spot: a window whose middle is not identified reads as a run of unrelated
            # steps, and the seq the agent asked for is the one it must not lose.
            neighbours = ([(row, "  ") for row in window.before]
                          + [(window.target, "→ ")]
                          + [(row, "  ") for row in window.after])
            context = "\n\nSurrounding steps:\n" + "\n".join(
                f"{marker}{row.get('seq_no')}  {row.get('event_type')}  "
                f"{str(row.get('label') or '')[:80]}"
                for row, marker in neighbours
            )

        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"{session_id}#{seq_no}"
                     + (f"\nLinks: {'; '.join(links)}" if links else "")
                     + f"\n\n{body}{context}"),
            data={"session_id": session_id, "seq_no": int(seq_no),
                  "shadowed": window.shadowed, "derived_from": window.derived_from,
                  "shadowed_by": window.shadowed_by, "paired_with": window.paired_with},
        )


@TOOL.register_module(force=True)
class SessionTraceTool(Tool):
    """Show which past runs belong together."""

    name: str = "session_trace_tool"
    description: str = _TRACE_DESCRIPTION
    guidance: str = _TRACE_GUIDANCE
    examples: List[str] = _TRACE_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads finished trace logs; writes nothing.")
    mutates: Optional[bool] = Field(default=False, description="Reads the record of past runs.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, session_id: str, **kwargs) -> Response:
        """Return a run's full trace.

        Args:
            session_id: From ``session_search_tool``.
        """
        record = session_manager.find(session_id)
        if record is None:
            return _not_found(session_id)

        siblings = session_manager.related(session_id)
        if not siblings:
            return Response(
                type=ResponseType.TOOL, success=True,
                message=(f"{record.session_id} is the only run recorded under session "
                         f"{record.project}; it delegated nothing that left its own log."),
                data={"session_id": session_id, "project": record.project, "related": []},
            )

        def describe(item) -> str:
            verdict = "?" if item.success is None else ("ok" if item.success else "FAILED")
            return (f"{item.session_id}  [{verdict}]  {item.started_at[:19]}  "
                    f"{'/'.join(item.agent_names) or '-'}  ({item.event_count} events)\n"
                    f"    task: {item.task or '(none recorded)'}")

        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Session {record.project} holds {len(siblings) + 1} run(s).\n\n"
                     f"you asked about:\n{describe(record)}\n\n"
                     f"filed alongside it:\n"
                     + "\n".join(describe(item) for item in siblings)
                     + "\n\nRead any of them with session_read_tool."),
            data={"session_id": session_id, "project": record.project,
                  "related": [item.model_dump() for item in siblings]},
        )


__all__ = [
    "INLINE_EVENT_CHARS",
    "SessionEventReadTool",
    "SessionEventSearchTool",
    "SessionReadTool",
    "SessionSearchTool",
    "SessionTraceTool",
]
