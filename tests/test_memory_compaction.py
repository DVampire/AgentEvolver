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
    return TieredMemory(base_dir="", recent_max=4, recent_fetch=2, **kwargs)


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
    assert state.compaction is None  # the bracket still closed


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


@pytest.mark.asyncio
async def test_a_native_claude_summary_replaces_the_window_without_resummarising(monkeypatch):
    memory, state = _memory(), _state()
    _fill(state, 9)

    async def native(*args, **kwargs):
        return {
            "summary": "claude checkpoint",
            "provider_state": {
                "anthropic": {
                    "compaction_blocks": [{"type": "compaction", "content": "claude checkpoint"}]
                }
            },
            "format": "anthropic.compact_20260112",
            "native": True,
        }

    async def must_not_run(*args, **kwargs):
        raise AssertionError("native summary was summarized a second time")

    recorded = []

    async def record(*args, **kwargs):
        recorded.append(kwargs.get("native"))

    monkeypatch.setattr(memory, "_native_checkpoint", native)
    monkeypatch.setattr(memory, "_summarise", must_not_run)
    monkeypatch.setattr(memory, "_record_fold", record)

    await memory._compact(state, down_to=2)

    assert list(state.working) == ["claude checkpoint"]
    assert len(state.recent) == 2
    assert recorded[0]["format"] == "anthropic.compact_20260112"


@pytest.mark.asyncio
async def test_an_opaque_native_checkpoint_still_gets_a_portable_text_companion(monkeypatch):
    """Responses state is replayable but unreadable and cannot carry a provider switch."""
    memory, state = _memory(), _state()
    _fill(state, 9)
    native_checkpoint = {
        "provider_state": {
            "responses": {
                "compaction_items": [{"type": "compaction", "encrypted_content": "opaque"}]
            }
        },
        "format": "openai.responses.compaction",
        "native": True,
    }

    async def native(*args, **kwargs):
        return native_checkpoint

    async def portable(items, existing):
        return "portable checkpoint"

    recorded = []

    async def record(*args, **kwargs):
        recorded.append(kwargs.get("native"))

    monkeypatch.setattr(memory, "_native_checkpoint", native)
    monkeypatch.setattr(memory, "_summarise", portable)
    monkeypatch.setattr(memory, "_record_fold", record)

    await memory._compact(state, down_to=2)

    assert list(state.working) == ["portable checkpoint"]
    assert len(state.recent) == 2
    assert recorded[-1] is native_checkpoint


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

    TieredMemory._append_recent(
        memory, state, MemoryRecord(ts="t", event="bash_tool result", detail=detail)
    )

    stored = state.recent[0].detail
    assert "saved at" in stored, "the way back to the full output was cut off"
    assert stored.startswith("XXX")  # head still leads
    assert "more characters not kept in memory" in stored  # and says what it dropped
    assert len(stored) < _RECORD_DETAIL_MAX + 200


# --------------------------------------------------------------------------- #
# A background write that fails must say so
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_detached_memory_task_reports_its_failure(monkeypatch):
    """A dropped task handle takes the exception with it.

    asyncio reports "Task exception was never retrieved" at interpreter shutdown — long
    after the run that lost the data has ended, and with nothing naming what was lost.
    Both callers write memory: todos the agent believes it recorded, and the compaction
    that keeps history bounded. Silence there is a run acting on a memory it does not
    have.
    """
    import asyncio

    import agentevolver.logger as logger_module
    from agentevolver.memory.default.tiered import _detached

    said = []
    monkeypatch.setattr(logger_module.logger, "warning", lambda message: said.append(message))

    async def fails():
        raise RuntimeError("persist failed")

    _detached(fails(), "todo update")
    await asyncio.sleep(0.05)

    assert said, "the failure vanished with the task handle"
    assert "todo update" in said[0], "the report does not name what was lost"
    assert "persist failed" in said[0]


@pytest.mark.asyncio
async def test_a_detached_task_that_succeeds_stays_quiet(monkeypatch):
    """Reporting success would make the log useless for finding the failures."""
    import asyncio

    import agentevolver.logger as logger_module
    from agentevolver.memory.default.tiered import _detached

    said = []
    monkeypatch.setattr(logger_module.logger, "warning", lambda message: said.append(message))

    async def works():
        return 1

    _detached(works(), "compaction")
    await asyncio.sleep(0.05)

    assert said == []


@pytest.mark.asyncio
async def test_cancelling_a_detached_task_is_not_a_failure(monkeypatch):
    """Shutdown cancels outstanding tasks; a warning per task would be noise at exit."""
    import asyncio

    import agentevolver.logger as logger_module
    from agentevolver.memory.default.tiered import _detached

    said = []
    monkeypatch.setattr(logger_module.logger, "warning", lambda message: said.append(message))

    async def slow():
        await asyncio.sleep(10)

    coro = slow()
    _detached(coro, "compaction")
    await asyncio.sleep(0)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0.05)

    assert said == []


# --------------------------------------------------------------------------- #
# Folding on demand, for a request that will not fit
# --------------------------------------------------------------------------- #
# The ordinary trigger counts records. A request that does not fit is over a token
# budget, and the two disagree: thirty small records sit under the count while three
# large ones sit over the budget. `compact()` is how whoever measured the budget asks
# for room without waiting for the count to catch up.


def test_folding_on_demand_does_not_wait_for_the_record_count(monkeypatch):
    """The count says there is nothing to do; the caller has measured otherwise."""
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    _fill(state, memory.recent_max)  # at the threshold, so `_compact` would idle

    monkeypatch.setattr(
        TieredMemory, "_summarise", staticmethod(lambda items, existing: _resolved("a summary"))
    )
    assert asyncio.run(memory.compact("s1")) is True
    assert len(state.recent) < memory.recent_max


def test_folding_stops_at_what_the_next_step_will_read(monkeypatch):
    """`recent_fetch` records reach the prompt.

    Folding past it buys space by removing what the next step is about to read, which
    trades an oversized request for a step that cannot see what it just did.
    """
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    _fill(state, memory.recent_fetch + 1)

    monkeypatch.setattr(
        TieredMemory, "_summarise", staticmethod(lambda items, existing: _resolved("a summary"))
    )
    asyncio.run(memory.compact("s1"))

    assert len(state.recent) >= memory.recent_fetch


def test_step_retention_keeps_complete_recent_steps(monkeypatch):
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    state.recent.append(MemoryRecord(ts="t", event="start", detail="task"))
    for step, calls in ((1, 2), (2, 1), (3, 3), (4, 2)):
        for call in range(calls):
            state.recent.append(
                MemoryRecord(
                    ts="t",
                    event=f"step {step} call {call}",
                    detail="d",
                    step=step,
                )
            )

    monkeypatch.setattr(
        TieredMemory, "_summarise", staticmethod(lambda items, existing: _resolved("a summary"))
    )
    assert asyncio.run(memory.compact("s1", keep_steps=2)) is True

    assert {record.step for record in state.recent} == {3, 4}
    assert sum(record.step == 3 for record in state.recent) == 3
    assert sum(record.step == 4 for record in state.recent) == 2


def test_step_retention_does_not_fold_when_only_the_exact_tail_exists():
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    for step in range(1, 4):
        state.recent.append(MemoryRecord(ts="t", event="call", detail="d", step=step))

    assert asyncio.run(memory.compact("s1", keep_steps=3)) is False


@pytest.mark.asyncio
async def test_an_assistant_only_turn_has_a_complete_step_retention_handle():
    """Closed turns without tools must still age into the next checkpoint."""
    from agentevolver.trace.types import TraceEvent, TraceEventType

    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    event = TraceEvent(
        event_type=TraceEventType.AGENT_CALL,
        session_id="s1",
        step_number=7,
        seq_no=42,
        assistant_text="I verified the fix.",
    )

    await memory.emit(event, "s1")

    assert [(record.step, record.seq) for record in state.recent] == [(7, 42)]
    assert state.recent[0].detail == "I verified the fix."


def test_a_history_already_at_that_floor_reports_that_it_folded_nothing():
    """`False` is what stops the caller asking again.

    A caller that reads "folded" from a fold that removed nothing rebuilds the same
    oversized request and asks once more, for as long as its budget lasts.
    """
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    _fill(state, memory.recent_fetch)

    assert asyncio.run(memory.compact("s1")) is False


def test_folding_an_unknown_session_is_not_an_error():
    """A run whose memory never started has nothing to fold, and saying so is the
    answer — raising here would replace an oversized-context report with a KeyError."""
    assert asyncio.run(_memory().compact("never-seen")) is False


def test_a_memory_that_keeps_no_history_folds_nothing():
    """The base class answers for every memory that has nothing to give up."""
    from agentevolver.memory.types import Memory

    assert asyncio.run(Memory(name="stateless").compact("s1")) is False


async def _resolved(value):
    return value


# --------------------------------------------------------------------------- #
# What a fold cost, carried on the fold
# --------------------------------------------------------------------------- #
# Stats, a training-sample budget and the UI all want to know how much a compaction
# bought. A consumer that derives it has to hold the replaced range to subtract from, so
# each would keep its own copy of what the summary shadowed — and they would disagree the
# first time one of them missed a fold.


def _folded(monkeypatch, records: int = 4, summary: str = "a short summary"):
    """Run one fold and return the trace event it recorded."""
    emitted: list = []
    memory, state = _memory(), _state()
    memory._sessions["s1"] = state
    _fill(state, records)

    from agentevolver.trace import trace_manager

    monkeypatch.setattr(
        TieredMemory, "_summarise", staticmethod(lambda items, existing: _resolved(summary))
    )
    monkeypatch.setattr(trace_manager, "surface_span", lambda *a, **k: [0, 1])

    async def emit(event, *a, **k):
        if (event.metadata or {}).get("type") == "compaction":
            emitted.append(event)

    monkeypatch.setattr(trace_manager, "emit", emit)
    for position, record in enumerate(state.recent):
        record.seq = position
    asyncio.run(memory.compact("s1"))
    return emitted[0] if emitted else None


def test_a_fold_records_what_it_cost(monkeypatch):
    event = _folded(monkeypatch)

    assert event is not None, "the fold recorded nothing"
    metadata = event.metadata
    assert metadata["tokens_before"] > metadata["tokens_after"]
    assert metadata["tokens_saved"] == metadata["tokens_before"] - metadata["tokens_after"]
    assert metadata["model_calls"] == 1
    assert 0 < metadata["savings_ratio"] <= 1


def test_a_summary_longer_than_what_it_replaced_is_rejected(monkeypatch):
    """Compaction must not pay for a model call and then expand future requests."""
    event = _folded(monkeypatch, records=3, summary="x" * 4_000)

    assert event is None


def test_one_pressure_event_makes_one_portable_summary_call():
    memory, state = _memory(), _state()
    _fill(state, 40)
    calls = []

    async def _summary(items, existing):
        calls.append((items, existing))
        return "one checkpoint"

    memory._summarise = _summary
    asyncio.run(memory._compact(state, down_to=2))

    assert len(calls) == 1
    assert len(state.recent) == 2


def test_the_stats_projection_totals_folds_without_recomputing_them(monkeypatch):
    """The consumer reads the number; it does not hold the replaced range to subtract."""
    from agentevolver.trace.stats import TraceStats, TraceStatsProjector
    from agentevolver.trace.types import TraceEvent, TraceEventType

    state = TraceStats(session_id="s1")
    for saved in (120, -30):
        TraceStatsProjector._reduce(
            TraceStatsProjector.__new__(TraceStatsProjector),
            state,
            TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id="s1",
                metadata={"type": "compaction", "records": 4, "tokens_saved": saved},
            ),
        )

    assert state.compactions == 2
    assert state.compaction_tokens_saved == 90


# --------------------------------------------------------------------------- #
# Two compactions at once
# --------------------------------------------------------------------------- #
def test_two_compactions_at_once_do_not_trip_over_each_other(monkeypatch):
    """The caller checks the flag, then hands the work to a detached task.

    Between those two the loop runs. Another record arrives, passes the same check, and
    a second task is created — both before either has set the flag. The two then
    interleaved: one's `finally` cleared `state.compaction` while the other was reading
    `state.compaction["started_at"]` for its progress marker, so a real run reported
    `compaction failed ('NoneType' object is not subscriptable)` and put its chunk back.

    Reproduced by awaiting inside the summariser, which is where the real one yields.
    """
    memory, state = _memory(), _state()
    _fill(state, 9)
    failures = []

    async def _summary(items, existing):
        await asyncio.sleep(0)  # the yield the real summariser takes
        return "a summary"

    def _warn(message):
        if "compaction failed" in str(message):
            failures.append(str(message))

    monkeypatch.setattr(TieredMemory, "_summarise", staticmethod(_summary))
    monkeypatch.setattr("agentevolver.memory.default.tiered.logger.warning", _warn)

    async def _both():
        await asyncio.gather(
            TieredMemory._compact(memory, state), TieredMemory._compact(memory, state)
        )

    asyncio.run(_both())

    assert not failures, f"concurrent compaction still fails: {failures}"
    assert state.compaction is None, "the bracket was left open"
    assert len(state.recent) <= memory.recent_max


def test_the_second_arrival_leaves_the_first_alone(monkeypatch):
    """It returns rather than folding again — the flag means "already running", and a
    second pass over the same records would fold what the first has already taken."""
    memory, state = _memory(), _state()
    _fill(state, 9)
    calls = []

    async def _summary(items, existing):
        calls.append(len(items))
        await asyncio.sleep(0)
        return "a summary"

    monkeypatch.setattr(TieredMemory, "_summarise", staticmethod(_summary))

    async def _both():
        await asyncio.gather(
            TieredMemory._compact(memory, state), TieredMemory._compact(memory, state)
        )

    asyncio.run(_both())
    solo, solo_state = _memory(), _state()
    _fill(solo_state, 9)
    calls_solo = []

    async def _summary_solo(items, existing):
        calls_solo.append(len(items))
        return "a summary"

    monkeypatch.setattr(TieredMemory, "_summarise", staticmethod(_summary_solo))
    asyncio.run(TieredMemory._compact(solo, solo_state))

    assert calls == calls_solo, (
        f"two concurrent calls folded differently from one: {calls} vs {calls_solo}"
    )


# --------------------------------------------------------------------------- #
# get(section=...) — the two tiers can be fetched apart, so the stable one
# (Working Memory) can ride in the request's cache prefix while the sliding
# Recent Steps stays out of it. Default "all" stays byte-identical.
# --------------------------------------------------------------------------- #
def _registered(memory, state):
    memory._sessions[state.session_id] = state
    return state


def test_get_section_splits_working_from_recent():
    memory = _memory()
    state = _registered(memory, _state())
    state.working.append("a compacted summary")
    _fill(state, 2)  # two recent records
    state.final_result = "the final result"

    all_ = asyncio.run(memory.get("s1", section="all"))
    stable = asyncio.run(memory.get("s1", section="stable"))
    volatile = asyncio.run(memory.get("s1", section="volatile"))

    # stable = only Working Memory
    assert "## Working Memory" in stable
    assert "a compacted summary" in stable
    assert "## Recent Steps" not in stable
    assert "Final Result" not in stable

    # volatile = Recent Steps + Final Result, never Working Memory
    assert "## Recent Steps" in volatile
    assert "## Working Memory" not in volatile
    assert "the final result" in volatile

    # the two partitions together reconstruct the default "all" render
    assert all_ == (stable + "\n\n" + volatile).strip()


def test_get_default_all_is_unchanged():
    """Backward-compat: the default fetch renders exactly as before the split."""
    memory = _memory()
    state = _registered(memory, _state())
    state.working.append("summary one")
    _fill(state, 2)
    out = asyncio.run(memory.get("s1"))
    assert out is not None
    assert out.index("## Working Memory") < out.index("## Recent Steps")


def test_get_stable_empty_when_no_working_memory():
    memory = _memory()
    state = _registered(memory, _state())
    _fill(state, 2)  # recent only, no working summaries
    assert asyncio.run(memory.get("s1", section="stable")) is None
    assert "## Recent Steps" in asyncio.run(memory.get("s1", section="volatile"))
