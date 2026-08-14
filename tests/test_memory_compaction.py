"""Compaction is a recorded transaction, and it never holes the history.

Two things used to be invisible. A compaction that died partway left `recent` shorter
with nothing anywhere saying so — the process-local `_compacting` flag died with the
process. And every failure, from an unreachable model to a bug in the summariser, came
out as the same one-line "compaction failed".

Separately: memory's own size backstop cut entries head-only, which silently removed
whatever a producer appended last. Since the tool pipeline appends the spill locator
there, a spilled result reached memory as an excerpt announcing missing text and no
longer saying where it went.
"""

import asyncio
import json

import pytest

from agentevolver.memory.default.tiered import (
    _RECORD_DETAIL_MAX,
    MemoryRecord,
    TieredMemory,
    _SessionState,
)


def _state(**kwargs):
    state = _SessionState(session_id="s1", task="t", file_path="", working_max=10)
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def _memory(**kwargs):
    return TieredMemory(base_dir="", recent_max=4, compact_chunk=2, recent_fetch=2, **kwargs)


def _fill(state, n):
    for i in range(n):
        state.recent.append(MemoryRecord(ts="t", event=f"event {i}", detail="d"))


# --------------------------------------------------------------------------- #
# The bracket
# --------------------------------------------------------------------------- #
def test_a_completed_compaction_leaves_no_open_bracket(monkeypatch):
    memory, state = _memory(), _state()
    _fill(state, 9)

    async def _summary(items, existing):
        return "a summary"

    monkeypatch.setattr(TieredMemory, "_summarise", staticmethod(_summary))
    asyncio.run(TieredMemory._compact(memory, state))

    assert state.compaction is None
    assert len(state.recent) <= memory.recent_max
    assert list(state.working)


def test_a_crash_mid_compaction_leaves_the_bracket_open():
    """The marker must survive the failure that interrupted it.

    Persisted before any record is taken and cleared only at the end, so a snapshot
    written while a compaction is in flight still carries it. Without that, a run
    that died here is indistinguishable from one that finished.
    """
    memory, state = _memory(), _state()
    _fill(state, 9)

    seen = {}

    async def _summary(items, existing):
        seen["bracket"] = dict(state.compaction or {})
        raise RuntimeError("process is going away")

    memory._summarise = _summary
    asyncio.run(TieredMemory._compact(memory, state))

    assert seen["bracket"], "the bracket was not open while summarising"
    assert "started_at" in seen["bracket"]


def test_a_failed_summary_puts_its_records_back():
    """Shorter history is acceptable; a hole in it is not."""
    memory, state = _memory(), _state()
    _fill(state, 9)
    before = [r.event for r in state.recent]

    async def _summary(items, existing):
        raise RuntimeError("model unreachable")

    memory._summarise = _summary
    asyncio.run(TieredMemory._compact(memory, state))

    assert [r.event for r in state.recent] == before
    assert not list(state.working)
    assert state.compaction is None          # the bracket still closed


def test_an_empty_summary_also_puts_its_records_back():
    """Nothing worth saying is a different outcome from a failure, and equally lossless."""
    memory, state = _memory(), _state()
    _fill(state, 9)
    before = [r.event for r in state.recent]

    async def _summary(items, existing):
        return ""

    memory._summarise = _summary
    asyncio.run(TieredMemory._compact(memory, state))

    assert [r.event for r in state.recent] == before


def test_a_partial_run_keeps_what_it_finished():
    """A failure on the second chunk must not undo the first."""
    memory, state = _memory(), _state()
    _fill(state, 12)
    calls = {"n": 0}

    async def _summary(items, existing):
        calls["n"] += 1
        if calls["n"] == 1:
            return "first summary"
        raise RuntimeError("gone")

    memory._summarise = _summary
    asyncio.run(TieredMemory._compact(memory, state))

    assert list(state.working) == ["first summary"]
    assert state.compaction is None


def test_cancellation_restores_and_propagates():
    """A cancelled compaction is not a failed one, and must not eat the cancellation."""
    memory, state = _memory(), _state()
    _fill(state, 9)
    before = [r.event for r in state.recent]

    async def _summary(items, existing):
        raise asyncio.CancelledError()

    memory._summarise = _summary
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(TieredMemory._compact(memory, state))

    assert [r.event for r in state.recent] == before
    assert state.compaction is None


# --------------------------------------------------------------------------- #
# The bracket is visible in what gets written
# --------------------------------------------------------------------------- #
def test_the_json_artifact_carries_an_open_bracket():
    from agentevolver.memory.default.general_memory_system import GeneralMemorySystem

    state = _state(compaction={"started_at": "12:00:00", "chunks": 2})
    rendered = json.loads(GeneralMemorySystem(base_dir="")._render(state))

    assert rendered["compaction"] == {"started_at": "12:00:00", "chunks": 2}


def test_the_html_artifact_warns_about_an_open_bracket():
    from agentevolver.memory.default.file_system_memory import FileSystemMemory

    state = _state(compaction={"started_at": "12:00:00", "chunks": 2})
    html = FileSystemMemory(base_dir="")._render_history(state)

    assert "did not finish" in html
    assert "12:00:00" in html


def test_a_closed_bracket_says_nothing():
    from agentevolver.memory.default.file_system_memory import FileSystemMemory

    html = FileSystemMemory(base_dir="")._render_history(_state())
    assert "did not finish" not in html
    assert "No history yet" in html


def test_an_emptied_history_still_reports_its_open_bracket():
    """The worst case: the compaction took everything and never put it back.

    Rendered without the warning this is indistinguishable from a session that has not
    done anything yet, which is the most misleading thing memory could say.
    """
    from agentevolver.memory.default.file_system_memory import FileSystemMemory

    state = _state(compaction={"started_at": "12:00:00", "chunks": 0})
    html = FileSystemMemory(base_dir="")._render_history(state)

    assert "did not finish" in html
    assert "No history yet" not in html


# --------------------------------------------------------------------------- #
# The size backstop keeps the tail
# --------------------------------------------------------------------------- #
def test_memory_truncation_keeps_the_spill_locator():
    """The locator is the last thing in a bounded tool result, so head-only loses it."""
    memory, state = _memory(), _state()
    locator = "[The full output is saved at `/output/.runtime/spill/s/abc-bash_tool.txt`.]"
    detail = ("X" * 30_000) + "\n\n" + locator

    TieredMemory._append_recent(memory, state, MemoryRecord(
        ts="t", event="bash_tool result", detail=detail))

    stored = state.recent[0].detail
    assert "saved at" in stored, "the way back to the full output was cut off"
    assert stored.startswith("XXX")                       # head still leads
    assert "more characters not kept in memory" in stored  # and says what it dropped
    assert len(stored) < _RECORD_DETAIL_MAX + 200
