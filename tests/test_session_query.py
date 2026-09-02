"""A finished run can be found and read back, in bounded pieces, without being altered.

Every run already wrote a full record of itself and nothing could read one, so each run
began blind and paid again for the last one's dead ends. The failures this file guards
are the ones that make a retrieval layer worse than none: a sub-agent's log folded into
its parent's so the run that did the work is unreachable; a capped result presented as a
complete one, which reads as "that was never done"; a compaction summary returned with no
way back to what it shadowed; and a whole log refused because one line of it was written
while the run was still going.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.session.query import MAX_HITS, session_query
from agentevolver.tool.default.observability.session_query import (
    INLINE_EVENT_CHARS,
    SessionEventReadTool,
    SessionEventSearchTool,
    SessionReadTool,
    SessionSearchTool,
    SessionTraceTool,
)

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Point the whole layout at a temp dir, so only logs written here are found."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    return tmp_path


def _event(seq: int, event_type: str, **fields) -> dict:
    """One stored row, with the fields the writer always emits."""
    row = {
        "id": f"e{seq}",
        "event_type": event_type,
        "session_id": fields.pop("session_id", ""),
        "task_id": "t1",
        "agent_name": fields.pop("agent_name", "meta_agent"),
        "label": f"{event_type} {seq}",
        "step_number": None,
        "action_index": None,
        "action_type": None,
        "action_name": None,
        "input": None,
        "output": None,
        "reasoning": None,
        "message": None,
        "success": None,
        "error": None,
        "duration_ms": None,
        "usage": None,
        "metadata": {},
        "timestamp": (_START + timedelta(seconds=seq)).isoformat(),
        "seq_no": seq,
        "surface_op": "append",
        "source_event_seqs": None,
        "fingerprint": None,
        "provenance": "live",
        "confidence": "high",
    }
    row.update(fields)
    return row


def _write(
    session_id: str,
    rows: list,
    *,
    owner: str = "local",
    project: str = "proj1",
    mtime: float | None = None,
) -> Path:
    """Write one run's log where trace would have written it."""
    trace = path_manager.get(P.SESSION_TRACE, owner=owner, session_id=project)
    trace.mkdir(parents=True, exist_ok=True)
    path = trace / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps({**row, "session_id": session_id}) + "\n" for row in rows),
        encoding="utf-8",
    )
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def _simple_run(session_id: str, task: str, answer: str, **kwargs) -> Path:
    """A three-event run: it was asked something, it did one thing, it answered."""
    return _write(
        session_id,
        [
            _event(0, "agent_start", input={"task": task}),
            _event(
                1,
                "tool_call",
                action_name="bash_tool",
                action_type="tool",
                message="pytest passed",
                metadata={"success": True, "call_id": "c1"},
            ),
            _event(2, "agent_end", message=answer, success=True),
        ],
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Where the logs are
# --------------------------------------------------------------------------- #
def test_the_layout_key_points_where_trace_actually_writes(tree):
    """P.SESSION_TRACE must agree with the directory a session's sandbox hands trace.

    Two places state where a run's events land: the sandbox composes
    ``<project>/log`` and trace appends ``/trace``, while the layout table declares the
    whole path. Nothing makes them agree, and if they drift the query layer searches a
    directory that is always empty — reporting "no past run matches" for every query,
    which is indistinguishable from a machine that has never run anything.
    """
    root = path_manager.get(P.SESSION, owner="local", session_id="s1")
    sandbox = ProjectSandbox.create(root, materialize=False)

    assert Path(sandbox.log_root) / "trace" == path_manager.get(
        P.SESSION_TRACE, owner="local", session_id="s1"
    )


def test_a_sub_agent_run_is_found_as_its_own_session(tree):
    """A delegate's log must not be folded into the directory it is filed under.

    Trace writes a sub-agent's events into its parent's trace directory, so a reader
    that treated one directory as one session would return the meta-agent's log — which
    records only *that* it delegated — and lose the log that records what was done. That
    is the exact run an agent searching for prior work is looking for.
    """
    _simple_run("parent", "delegate the fibonacci work", "done")
    _simple_run("code_agent-99", "write fibonacci.py", "wrote it")

    found = {record.session_id for record in session_query.sessions()}
    assert found == {"parent", "code_agent-99"}
    assert {r.project for r in session_query.sessions()} == {"proj1"}


def test_runs_come_back_newest_first(tree):
    """Every cap below cuts from the end, so the order decides what gets dropped."""
    _simple_run("older", "old work", "ok", mtime=1_000_000)
    _simple_run("newer", "new work", "ok", mtime=2_000_000)

    assert [r.session_id for r in session_query.sessions()] == ["newer", "older"]


def test_related_runs_are_the_ones_filed_together(tree):
    """Co-location is the lineage that exists; `parent_session_id` is not a link.

    An `agent_start` carries `parent_session_id`, and it is tempting to follow — but it
    names the parent *agent runtime* ("meta_agent-b6bec114"), not a trace session id, so
    following it resolves to nothing and the two runs look unrelated.
    """
    _simple_run("parent", "delegate", "done")
    _simple_run("child", "do the work", "did it", project="proj1")
    _simple_run("elsewhere", "unrelated", "ok", project="proj2")

    assert [r.session_id for r in session_query.related("parent")] == ["child"]


# --------------------------------------------------------------------------- #
# Searching
# --------------------------------------------------------------------------- #
def test_a_session_matches_terms_spread_across_its_events(tree):
    """A description is spread over the task, the work, and the answer.

    Applying the event rule here — every term in one event — is the tempting
    simplification, and it finds nothing: no single event of a real run contains both
    what was asked and what came back.
    """
    _write(
        "spread",
        [
            _event(0, "agent_start", input={"task": "analyse the penguins dataset"}),
            _event(1, "tool_call", action_name="bash_tool", message="matplotlib figure saved"),
            _event(2, "agent_end", message="four charts produced", success=True),
        ],
    )

    page = session_query.search_sessions("penguins matplotlib")
    assert [hit.record.session_id for hit in page.sessions] == ["spread"]
    assert page.sessions[0].best is not None, "a hit must say where to start reading"


def test_an_event_hit_carries_every_term_itself(tree):
    """An event hit is a coordinate to read from, so half a match is the wrong place.

    Reusing the session rule here would return the run's first event for any query the
    run as a whole satisfies, and the agent would read an `agent_start` while looking
    for the traceback.
    """
    _write(
        "spread",
        [
            _event(0, "agent_start", input={"task": "analyse the penguins dataset"}),
            _event(1, "tool_call", action_name="bash_tool", message="matplotlib figure saved"),
        ],
    )

    assert session_query.search_events("penguins matplotlib").events == []
    hits = session_query.search_events("matplotlib figure").events
    assert [hit.seq_no for hit in hits] == [1]


def test_a_capped_search_says_it_was_capped(tree):
    """Silence here reads as "nothing more exists", which is the opposite of the truth.

    An agent that takes a capped list for the whole corpus concludes the work was never
    done and redoes it — the precise failure this module exists to prevent.
    """
    for index in range(5):
        _simple_run(f"run{index}", "shared subject matter", "ok", project=f"p{index}")

    page = session_query.search_sessions("shared subject", limit=2)
    assert len(page.sessions) == 2
    assert page.truncated


def test_the_model_cannot_raise_the_result_cap(tree):
    """A limit the caller sets is a limit that gets set to 1000 the first time a
    search looks thin, and the prompt pays for it on every later turn."""
    for index in range(3):
        _simple_run(f"run{index}", "shared subject matter", "ok", project=f"p{index}")

    page = session_query.search_sessions("shared subject", limit=10_000)
    assert len(page.sessions) <= MAX_HITS


def test_an_empty_query_matches_nothing_rather_than_everything(tree):
    """`all(...)` over no terms is True, so the natural implementation returns the
    entire corpus for a blank query — the largest possible answer to the least
    specific question."""
    _simple_run("run", "some work", "ok")

    assert session_query.search_sessions("").sessions == []
    assert session_query.search_events("   ").events == []


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def test_the_outline_shows_a_summary_in_place_of_what_it_shadowed(tree):
    """The default reading is the history as that run's agent saw it.

    A compaction summary declares `{"op": "replace"}` over a range and the originals
    stay in the file. Listing the file in write order would show both the summary and
    the three events it stands for — a conversation that never happened, and one that
    reads as the agent having said everything twice.
    """
    _write(
        "compacted",
        [
            _event(0, "agent_start", input={"task": "long task"}),
            _event(1, "tool_call", action_name="bash_tool", message="step one"),
            _event(2, "tool_call", action_name="bash_tool", message="step two"),
            _event(
                3,
                "agent_call",
                message="summary of the work so far",
                surface_op={"op": "replace", "start": 0, "end": 2},
                source_event_seqs=[0, 1, 2],
            ),
        ],
    )

    folded = session_query.outline("compacted")
    assert [entry.seq_no for entry in folded.entries] == [3]
    raw = session_query.outline("compacted", surface_only=False)
    assert [entry.seq_no for entry in raw.entries] == [0, 1, 2, 3]


def test_a_log_whose_surface_will_not_fold_is_still_readable(tree):
    """Refusing it would hide the session someone is investigating *because* it is odd.

    An uncited replacement means the log cannot be read the way its writer intended, so
    the fold rightly raises. Propagating that to the reader turns a diagnosable log into
    an unopenable one; falling back silently is worse still, since shadowed originals
    would then read as live history.
    """
    _write(
        "broken",
        [
            _event(0, "agent_start", input={"task": "x"}),
            _event(
                1,
                "agent_call",
                message="summary citing nothing",
                surface_op={"op": "replace", "start": 0, "end": 0},
            ),
        ],
    )

    outline = session_query.outline("broken")
    assert [entry.seq_no for entry in outline.entries] == [0, 1]
    assert outline.surface_error, "the reader must be told which reading it got"
    assert outline.surface_only is False


def test_an_event_read_gives_the_way_back_from_a_summary(tree):
    """A summary with no route to its originals is a lossy read of a lossless log.

    The log keeps every event a compaction shadowed; `source_event_seqs` is what says
    which ones. Without both directions exposed, an agent that finds the summary can
    see that detail was dropped and cannot get any of it back.
    """
    _write(
        "compacted",
        [
            _event(0, "agent_start", input={"task": "long task"}),
            _event(1, "tool_call", action_name="bash_tool", message="step one"),
            _event(
                2,
                "agent_call",
                message="summary",
                surface_op={"op": "replace", "start": 0, "end": 1},
                source_event_seqs=[0, 1],
            ),
        ],
    )

    summary = session_query.event_window("compacted", 2)
    assert summary.shadowed == [0, 1]
    assert summary.derived_from == [0, 1]
    original = session_query.event_window("compacted", 0)
    assert original.shadowed_by == 2


def test_a_result_is_linked_to_the_call_it_answers(tree):
    """`tool_start` holds the arguments and `tool_call` holds the output.

    Reading either alone answers half the question — what was attempted, or what came
    back — and pairing them by position breaks the moment two calls share a step, which
    is why the link follows `call_id`.
    """
    _write(
        "paired",
        [
            _event(
                0,
                "tool_start",
                action_name="bash_tool",
                step_number=0,
                action_index=0,
                input={"command": "pytest"},
                surface_op=None,
                metadata={"call_id": "abc"},
            ),
            _event(
                1,
                "tool_start",
                action_name="bash_tool",
                step_number=0,
                action_index=1,
                input={"command": "ruff"},
                surface_op=None,
                metadata={"call_id": "def"},
            ),
            _event(
                2,
                "tool_call",
                action_name="bash_tool",
                step_number=0,
                action_index=1,
                message="ruff clean",
                metadata={"call_id": "def"},
            ),
        ],
    )

    assert session_query.event_window("paired", 2).paired_with == 1


def test_a_log_still_being_written_reads_rather_than_failing(tree):
    """The last line of a live run's log is regularly half a record.

    Trace appends and flushes per event, so a reader arriving mid-write sees a truncated
    JSON object. Refusing the file over it makes the currently-running session — the one
    most worth looking at — the only one that cannot be read.
    """
    path = _simple_run("live", "work in progress", "not finished")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "e3", "event_type": "tool_ca')

    record = session_query.find("live")
    assert record.event_count == 3
    assert record.unreadable_lines == 1, "a dropped line must be counted, not hidden"


def test_a_run_with_no_end_event_is_not_reported_as_failed(tree):
    """ "We never found out" and "it failed" lead to different next moves.

    A killed or still-running log has no `agent_end`. Defaulting its verdict to False —
    the obvious way to type the field as a bool — tells an agent that an approach was
    tried and did not work, when in fact nothing was ever concluded about it.
    """
    _write("killed", [_event(0, "agent_start", input={"task": "interrupted"})])

    assert session_query.find("killed").success is None


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #
def test_an_oversized_event_is_parked_rather_than_pasted(tree):
    """One event can be an entire build log, and pasting it costs the whole context.

    Clipping alone would destroy the part it drops, leaving the agent no way to reach
    the middle of the result it just found — its only recourse being to re-run the
    original command and be truncated identically.
    """
    _write(
        "huge",
        [
            _event(0, "agent_start", input={"task": "build"}),
            _event(1, "tool_call", action_name="bash_tool", message="X" * (INLINE_EVENT_CHARS * 3)),
        ],
    )

    response = asyncio.run(SessionEventReadTool()(session_id="huge", seq_no=1))
    assert response.success
    assert len(response.message) < INLINE_EVENT_CHARS * 2
    assert "omitted inline as one complete unit" in response.message
    assert "XXX" not in response.message
    locator = next(part for part in response.message.split("`") if part.endswith(".json"))
    assert Path(locator).read_text(encoding="utf-8").count("X") == INLINE_EVENT_CHARS * 3


def test_naming_a_run_that_does_not_exist_reports_what_does(tree):
    """A bare "not found" cannot distinguish a typo from an empty corpus.

    Both readings lead the agent to guess another id, and only one of them can ever
    work. Listing what is actually on disk ends the guessing in one call.
    """
    _simple_run("real_run", "some work", "ok")

    response = asyncio.run(SessionReadTool()(session_id="typo"))
    assert response.success is False
    assert "real_run" in response.message


def test_reading_a_past_run_never_touches_its_log(tree):
    """The record must survive being read.

    Every tool here opens files the framework depends on for training data and audit.
    A reader that opened one for append — or rewrote it while normalising — would
    corrupt the evidence, and the damage would surface much later as a log that no
    longer folds.
    """
    _write(
        "compacted",
        [
            _event(0, "agent_start", input={"task": "long task"}),
            _event(1, "tool_call", action_name="bash_tool", message="step one"),
            _event(
                2,
                "agent_call",
                message="summary",
                surface_op={"op": "replace", "start": 0, "end": 1},
                source_event_seqs=[0, 1],
            ),
        ],
    )
    trace = path_manager.get(P.SESSION_TRACE, owner="local", session_id="proj1")
    before = {path: path.read_bytes() for path in trace.glob("*.jsonl")}

    async def read_everything():
        await SessionSearchTool()(query="long task")
        await SessionEventSearchTool()(query="step one")
        await SessionReadTool()(session_id="compacted")
        await SessionEventReadTool()(session_id="compacted", seq_no=2, before=2, after=2)
        await SessionTraceTool()(session_id="compacted")

    asyncio.run(read_everything())

    assert {path: path.read_bytes() for path in trace.glob("*.jsonl")} == before


def test_every_session_query_tool_only_reports(tree):
    """These read a record; declaring otherwise costs them a permission they never need.

    `mutates` is what lets an agent see the ratio of its own measuring to its own
    changing. A read-only tool typed as a mutation makes a run of pure investigation
    look like a run of work.
    """
    for cls in (
        SessionSearchTool,
        SessionEventSearchTool,
        SessionReadTool,
        SessionEventReadTool,
        SessionTraceTool,
    ):
        tool = cls()
        assert tool.permission_mode == "read_only", tool.name
        assert tool.mutates is False, tool.name
