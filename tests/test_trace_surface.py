"""The log can now say that one event stands for a run of earlier ones.

Two gaps made that impossible. `seq_no` was declared and never assigned, so nothing
could name an event by position — "somewhere earlier in the file" is not a citation.
And no event could declare how it joined the history, so after a compaction memory's
history had a summary where a dozen records used to be, the trace still had the dozen,
and nothing related the two.

A surface reconciles them without deleting anything: `append` joins at the tail,
`replace` stands in for a range, and the replaced events stay in the log exactly as
written. One log, two readings.
"""

import asyncio

import pytest

from agentevolver.trace.surface import (
    APPEND,
    SurfaceError,
    fold_surface,
    replace_op,
    shadowed_by,
    surface_events,
)
from agentevolver.trace.types import TraceEvent, TraceEventType


def _event(seq, op=APPEND, cites=None, label=""):
    return TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", seq_no=seq,
        label=label or f"e{seq}", surface_op=op, source_event_seqs=cites,
    )


# --------------------------------------------------------------------------- #
# Sequence numbers
# --------------------------------------------------------------------------- #
def test_emit_stamps_a_position():
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    manager._queue, manager._running = _Q(), True
    events = [TraceEvent(event_type=TraceEventType.CUSTOM, session_id="s") for _ in range(3)]
    for event in events:
        asyncio.run(manager.emit(event))

    assert [e.seq_no for e in events] == [0, 1, 2]


def test_sessions_are_numbered_independently():
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    manager._queue, manager._running = _Q(), True
    a1 = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="a")
    b1 = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="b")
    a2 = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="a")
    for event in (a1, b1, a2):
        asyncio.run(manager.emit(event))

    assert (a1.seq_no, a2.seq_no) == (0, 1)
    assert b1.seq_no == 0


def test_numbering_continues_after_a_restart():
    """Restarting at 0 would give two events the same position, and a citation two meanings."""
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    class _Writer:
        _session_meta = {"s": {"event_count": 42}}

    manager._queue, manager._running, manager._writer = _Q(), True, _Writer()
    event = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="s")
    asyncio.run(manager.emit(event))

    assert event.seq_no == 42


def test_history_bearing_constructors_join_the_surface():
    """The surface must mean something narrower than "the log"."""
    from agentevolver.trace.types import (
        agent_call_event, agent_end_event, agent_start_event,
        skill_call_event, tool_call_event, tool_start_event,
    )

    joins = [
        agent_start_event("s", "t", "a", "task"),
        agent_end_event("s", "t", "a", True, "r"),
        tool_call_event("s", "t", "a", 1, 0, "bash", None, True),
        skill_call_event("s", "t", "a", 1, 0, "sk", None, True),
    ]
    log_only = [
        agent_call_event("s", "t", "a", 1),                       # a step marker
        tool_start_event("s", "t", "a", 1, 0, "bash", {}),        # the call, not its result
    ]

    assert all(e.surface_op == APPEND for e in joins)
    assert all(e.surface_op is None for e in log_only)


# --------------------------------------------------------------------------- #
# The fold
# --------------------------------------------------------------------------- #
def test_appends_are_the_surface_in_order():
    events = [_event(i) for i in range(4)]
    assert fold_surface(events)["nodes"] == [0, 1, 2, 3]


def test_log_only_events_never_join():
    events = [_event(0), _event(1, op=None), _event(2)]
    assert fold_surface(events)["nodes"] == [0, 2]


def test_a_replacement_stands_in_for_its_range():
    events = [_event(i) for i in range(5)]
    events.append(_event(5, op=replace_op(1, 3), cites=[1, 2, 3]))

    fold = fold_surface(events)
    assert fold["nodes"] == [0, 5, 4]                 # in place, not appended at the tail
    assert fold["replacements"][0]["shadowed"] == [1, 2, 3]


def test_the_replaced_events_are_still_in_the_log():
    """Shadowed, not deleted — which is what makes a summary auditable."""
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(0, 2), cites=[0, 1, 2]))

    assert [e.seq_no for e in events] == [0, 1, 2, 3, 4]
    assert shadowed_by(events, 4) == [0, 1, 2]
    assert [e.seq_no for e in surface_events(events)] == [4, 3]


def test_replacements_compose():
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(0, 1), cites=[0, 1]))
    events.append(_event(5, op=replace_op(4, 3), cites=[4, 2, 3]))

    assert fold_surface(events)["nodes"] == [5]


# --------------------------------------------------------------------------- #
# What the fold refuses
# --------------------------------------------------------------------------- #
def test_a_replacement_must_cite_what_it_shadows():
    """Uncited, the originals behind a summary are unreachable — the log still holds
    them, but nothing says which ones this summary stands for."""
    events = [_event(i) for i in range(3)]
    events.append(_event(3, op=replace_op(0, 2), cites=[0, 2]))     # 1 missing

    with pytest.raises(SurfaceError, match="does not cite"):
        fold_surface(events)


def test_replacing_an_already_replaced_range_is_refused():
    events = [_event(i) for i in range(3)]
    events.append(_event(3, op=replace_op(0, 1), cites=[0, 1]))
    events.append(_event(4, op=replace_op(0, 1), cites=[0, 1]))     # 0 and 1 are gone

    with pytest.raises(SurfaceError, match="not on the current surface"):
        fold_surface(events)


def test_a_backwards_range_is_refused():
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(3, 1), cites=[1, 2, 3]))

    with pytest.raises(SurfaceError, match="backwards"):
        fold_surface(events)


def test_a_surface_event_without_a_position_is_refused():
    with pytest.raises(SurfaceError, match="no seq_no"):
        fold_surface([_event(None)])


def test_an_unknown_op_is_refused_rather_than_guessed():
    with pytest.raises(SurfaceError, match="unknown surface op"):
        fold_surface([_event(0, op="prepend")])


# --------------------------------------------------------------------------- #
# Compaction closes the loop
# --------------------------------------------------------------------------- #
def test_compaction_records_its_fold_in_the_log():
    """After a fold, the log and memory's history can finally be lined up."""
    from agentevolver.memory.default.tiered import MemoryRecord, TieredMemory, _SessionState

    memory = TieredMemory(base_dir="", recent_max=4, compact_chunk=2, recent_fetch=2)
    state = _SessionState(session_id="s1", task="t", file_path="", working_max=10)
    for i in range(9):
        state.recent.append(MemoryRecord(ts="t", event=f"e{i}", detail="d", seq=i))

    emitted = []

    async def _summary(items, existing):
        return "a summary"

    memory._summarise = _summary

    class _Manager:
        async def emit(self, event):
            emitted.append(event)

    import agentevolver.trace as trace_pkg
    real = trace_pkg.trace_manager
    trace_pkg.trace_manager = _Manager()
    try:
        asyncio.run(TieredMemory._compact(memory, state))
    finally:
        trace_pkg.trace_manager = real

    assert emitted, "the fold was not recorded"
    first = emitted[0]
    assert first.surface_op == {"op": "replace", "start": 0, "end": 1}
    assert first.source_event_seqs == [0, 1]
    assert first.message == "a summary"


def test_records_without_a_position_are_not_cited():
    """Records predating sequence numbering cannot be cited, so no claim is made."""
    from agentevolver.memory.default.tiered import MemoryRecord, TieredMemory, _SessionState

    memory = TieredMemory(base_dir="", recent_max=4, compact_chunk=2, recent_fetch=2)
    state = _SessionState(session_id="s1", task="t", file_path="", working_max=10)
    for i in range(9):
        state.recent.append(MemoryRecord(ts="t", event=f"e{i}", detail="d"))   # no seq

    emitted = []

    async def _summary(items, existing):
        return "a summary"

    memory._summarise = _summary

    class _Manager:
        async def emit(self, event):
            emitted.append(event)

    import agentevolver.trace as trace_pkg
    real = trace_pkg.trace_manager
    trace_pkg.trace_manager = _Manager()
    try:
        asyncio.run(TieredMemory._compact(memory, state))
    finally:
        trace_pkg.trace_manager = real

    assert emitted == []
