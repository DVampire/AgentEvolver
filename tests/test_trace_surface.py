"""The log can now say that one event stands for a run of earlier ones.

Two gaps made that impossible. ``seq_no`` was declared and never assigned, so nothing
could name an event by position — "somewhere earlier in the file" is not a citation. And
no event could declare how it joined the history, so after a compaction memory's history
had a summary where a dozen records used to be, the trace still had the dozen, and nothing
related the two.

A surface reconciles them without deleting anything: ``append`` joins at the tail,
``replace`` stands in for a range, and the replaced events stay in the log exactly as
written. One log, two readings — the surface for what the history now says, the raw append
order for what actually happened.

The strictness is deliberate and one-sided. ``fold_surface`` reads a stored log and
refuses one it cannot interpret, because guessing produces a history nobody wrote. The
manager's live surface is forgiving, because refusing an event that is still in flight
loses the event itself. Both halves are checked here.
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
    transcript_events,
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
def test_emitting_stamps_an_event_with_its_position():
    """Numbering happens on emit, not in the writer, and that placement is the point.

    The writer drains the queue asynchronously, so numbering there would hand every
    subscriber an event whose position is still unknown — and a subscriber cannot cite
    what has no number yet.
    """
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


def test_each_session_numbers_its_own_events_from_zero():
    """A position is only meaningful within one session's log.

    One manager serves every concurrent session, so a single global counter would leave
    each session's numbers full of gaps that vary with how the sessions interleaved — and
    two runs of the same task would produce different citations for the same events. The
    interleaved order here is what a counter shared across sessions would fail on.
    """
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
    """Restarting at 0 would give two events the same position, and a citation two meanings.

    A resumed session is a new process with an empty counter, so the first number is
    seeded from the writer's index instead of from nothing. 42 is a session already well
    underway: the next event must be 42, not 0.
    """
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    class _Writer:
        def next_seq(self, session_id):
            assert session_id == "s"
            return 42

    manager._queue, manager._running, manager._writer = _Q(), True, _Writer()
    event = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="s")
    asyncio.run(manager.emit(event))

    assert event.seq_no == 42


def test_history_bearing_constructors_join_the_surface():
    """The surface must mean something narrower than "the log".

    Which constructors declare a join is the whole definition, and it is a judgement that
    lives in six separate places — so it is asserted from the constructors rather than
    from a list someone maintains. The distinction is not "important" versus "not": a
    tool's *arguments* are as real as its result, they simply belong to the assistant turn
    that already joined, so counting them separately would put the same turn on the
    surface twice.
    """
    from agentevolver.trace.types import (
        agent_call_event, agent_end_event, agent_start_event,
        skill_call_event, tool_call_event, tool_start_event,
    )

    joins = [
        agent_start_event("s", "t", "a", "task"),                 # the task
        agent_call_event("s", "t", "a", 1),                       # the assistant's turn
        agent_end_event("s", "t", "a", True, "r"),                # the final answer
        tool_call_event("s", "t", "a", 1, 0, "bash", None, True),  # a result
        skill_call_event("s", "t", "a", 1, 0, "sk", None, True),
    ]
    # A call is part of the assistant's turn, not a message of its own, so the event
    # carrying its arguments stays log-only and is joined in when projecting.
    log_only = [tool_start_event("s", "t", "a", 1, 0, "bash", {})]

    assert all(e.surface_op == APPEND for e in joins)
    assert all(e.surface_op is None for e in log_only)


# --------------------------------------------------------------------------- #
# The fold
# --------------------------------------------------------------------------- #
def test_appends_are_the_surface_in_order():
    """With no replacements the surface is just the log, which is the base case everything else edits."""
    events = [_event(i) for i in range(4)]
    assert fold_surface(events)["nodes"] == [0, 1, 2, 3]


def test_an_event_with_no_declaration_never_joins():
    """A missing ``surface_op`` means log-only, never "append by default".

    Defaulting to append is the tempting reading — most events do append — and it would
    quietly put bookkeeping records into the conversation, then let a compaction summarise
    them as if they had been turns.
    """
    events = [_event(0), _event(1, op=None), _event(2)]
    assert fold_surface(events)["nodes"] == [0, 2]


def test_a_replacement_stands_in_for_its_range():
    """A summary takes the position of what it summarised, not a place at the end.

    Appending it instead would reorder the history: the summary of events 1 through 3
    would appear *after* event 4, so a reader would see the later event described before
    the earlier ones it followed.
    """
    events = [_event(i) for i in range(5)]
    events.append(_event(5, op=replace_op(1, 3), cites=[1, 2, 3]))

    fold = fold_surface(events)
    assert fold["nodes"] == [0, 5, 4]                 # in place, not appended at the tail
    assert fold["replacements"][0]["shadowed"] == [1, 2, 3]


def test_the_replaced_events_are_still_in_the_log():
    """Shadowed, not deleted — which is what makes a summary auditable.

    All three readings are asserted together because that is the claim: the raw log is
    untouched, the way back from the summary to its originals still resolves, and the
    surface shows the summary in their place. A design that deleted the originals would
    satisfy the third alone.
    """
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(0, 2), cites=[0, 1, 2]))

    assert [e.seq_no for e in events] == [0, 1, 2, 3, 4]
    assert shadowed_by(events, 4) == [0, 1, 2]
    assert [e.seq_no for e in surface_events(events)] == [4, 3]


def test_a_summary_can_itself_be_summarised():
    """Compaction runs repeatedly over a long session, so replacements have to nest.

    The second replacement names event 4 — itself a summary — as the start of its range.
    That only works if the fold treats a replacement's own seq as an ordinary surface node
    afterwards; treating replacements as terminal would make the second compaction of any
    session fail.
    """
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(0, 1), cites=[0, 1]))
    events.append(_event(5, op=replace_op(4, 3), cites=[4, 2, 3]))

    assert fold_surface(events)["nodes"] == [5]


# --------------------------------------------------------------------------- #
# What the fold refuses
# --------------------------------------------------------------------------- #
def test_a_replacement_must_cite_what_it_shadows():
    """Uncited, the originals behind a summary are unreachable — the log still holds
    them, but nothing says which ones this summary stands for.

    Under-citing is the realistic mistake, not a missing citation list: a producer cites
    the records *it* knows about, while the range it replaces also covers events other
    producers put there. Event 1 is missing here for exactly that reason.
    """
    events = [_event(i) for i in range(3)]
    events.append(_event(3, op=replace_op(0, 2), cites=[0, 2]))     # 1 missing

    with pytest.raises(SurfaceError, match="does not cite"):
        fold_surface(events)


def test_replacing_an_already_replaced_range_is_refused():
    """A range names surface positions, and the first replacement removed these.

    Two compactions racing on the same session produce this. Silently re-applying the
    second would have it shadow whatever now sits at those coordinates — a different set
    of events than the summary was written from.
    """
    events = [_event(i) for i in range(3)]
    events.append(_event(3, op=replace_op(0, 1), cites=[0, 1]))
    events.append(_event(4, op=replace_op(0, 1), cites=[0, 1]))     # 0 and 1 are gone

    with pytest.raises(SurfaceError, match="not on the current surface"):
        fold_surface(events)


def test_a_backwards_range_is_refused():
    """Both edges exist on the surface, so only their order is wrong.

    That is what makes it worth its own case: an index-based implementation would happily
    slice an empty span and drop the replacement without complaint, leaving a summary in
    the log that stands for nothing.
    """
    events = [_event(i) for i in range(4)]
    events.append(_event(4, op=replace_op(3, 1), cites=[1, 2, 3]))

    with pytest.raises(SurfaceError, match="backwards"):
        fold_surface(events)


def test_a_surface_event_without_a_position_is_refused():
    """An event that claims to be history but cannot be placed in it.

    Skipping it would look harmless and would silently drop a turn from the history;
    numbering it on the spot would invent a position that nothing else in the log agrees
    with.
    """
    with pytest.raises(SurfaceError, match="no seq_no"):
        fold_surface([_event(None)])


def test_an_unknown_op_is_refused_rather_than_guessed():
    """A declaration the reader does not understand is not a declaration it may ignore.

    Falling back to append for an unrecognised op would read a future op — or a typo —
    as an ordinary turn, and the resulting history is wrong in a way nothing reports.
    """
    with pytest.raises(SurfaceError, match="unknown surface op"):
        fold_surface([_event(0, op="prepend")])


# --------------------------------------------------------------------------- #
# Compaction closes the loop
# --------------------------------------------------------------------------- #
def test_compaction_records_its_fold_in_the_log():
    """After a fold, the log and memory's history can finally be lined up.

    This is the gap the surface was built for: memory drops the summarised records from
    its window, the trace log keeps them, and before this nothing marked them as folded.
    The emitted event is the marker — a replacement over the range, citing every seq the
    surface says is in it. The manager is faked because the real one owns the live surface
    and the point here is that memory asks it, rather than citing only its own records.
    """
    from agentevolver.memory.default.tiered import MemoryRecord, TieredMemory, _SessionState

    # recent_max=4 with 9 records guarantees compaction actually runs.
    memory = TieredMemory(base_dir="", recent_max=4, recent_fetch=2)
    state = _SessionState(session_id="s1", task="t", file_path="", working_max=10)
    for i in range(9):
        state.recent.append(MemoryRecord(ts="t", event=f"e{i}", detail="d", seq=i))

    emitted = []

    async def _summary(items, existing):
        return "a summary"

    memory._summarise = _summary

    class _Manager:
        """Stands in for the real manager, which owns the live surface."""

        def __init__(self):
            self.nodes = list(range(9))

        def surface_span(self, session_id, start, end):
            i, j = self.nodes.index(start), self.nodes.index(end)
            return self.nodes[i : j + 1]

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
    first = next(
        event for event in emitted
        if (event.metadata or {}).get("type") == "compaction"
    )
    assert first.surface_op == {"op": "replace", "start": 0, "end": 4}
    assert first.source_event_seqs == [0, 1, 2, 3, 4]
    assert first.message == "a summary"


def test_records_without_a_position_are_not_cited():
    """Records predating sequence numbering cannot be cited, so no claim is made.

    Making the claim anyway is what would hurt: a replacement that cites the wrong seqs is
    accepted by the fold and produces a history in which a summary shadows events it was
    not built from. Silence is recoverable; a wrong citation is not.
    """
    from agentevolver.memory.default.tiered import MemoryRecord, TieredMemory, _SessionState

    memory = TieredMemory(base_dir="", recent_max=4, recent_fetch=2)
    state = _SessionState(session_id="s1", task="t", file_path="", working_max=10)
    for i in range(9):
        state.recent.append(MemoryRecord(ts="t", event=f"e{i}", detail="d"))   # no seq

    emitted = []

    async def _summary(items, existing):
        return "a summary"

    memory._summarise = _summary

    class _Manager:
        def surface_span(self, session_id, start, end):
            return [start, end]

        async def emit(self, event):
            emitted.append(event)

    import agentevolver.trace as trace_pkg
    real = trace_pkg.trace_manager
    trace_pkg.trace_manager = _Manager()
    try:
        asyncio.run(TieredMemory._compact(memory, state))
    finally:
        trace_pkg.trace_manager = real

    assert not any(
        (event.metadata or {}).get("type") == "compaction" for event in emitted
    )


# --------------------------------------------------------------------------- #
# The live surface on the manager
# --------------------------------------------------------------------------- #
def _live_manager():
    """A manager wired up enough to emit, with the queue and writer stubbed out."""
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    manager._queue, manager._running = _Q(), True
    return manager


def test_the_manager_tracks_the_surface_as_it_emits():
    """The live surface has to agree with what folding the same events would give.

    Two implementations of one rule is how they drift — the emit path is the only place
    that sees events in order, so it maintains the surface itself rather than re-folding
    the log on every question. The log-only event at the end is the case both must skip.
    """
    manager = _live_manager()
    for _ in range(3):
        asyncio.run(manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="s", surface_op=APPEND)))
    asyncio.run(manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", surface_op=None)))   # log-only

    assert manager.surface("s") == [0, 1, 2]


def test_a_replacement_advances_the_live_surface_in_place():
    """The same in-place rule as the fold, on the path that runs during a live session."""
    manager = _live_manager()
    for _ in range(4):
        asyncio.run(manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="s", surface_op=APPEND)))
    asyncio.run(manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s",
        surface_op=replace_op(1, 2), source_event_seqs=[1, 2])))

    assert manager.surface("s") == [0, 4, 3]


def test_surface_span_returns_everything_in_the_range():
    """What a producer needs before replacing: its own records are only part of it.

    Memory holds one record per result while the surface also carries the assistant turn
    that produced it, so a producer citing only what it remembers would under-cite and the
    fold would refuse the log. Asking the owner of the surface is the fix.
    """
    manager = _live_manager()
    for _ in range(5):
        asyncio.run(manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="s", surface_op=APPEND)))

    assert manager.surface_span("s", 1, 3) == [1, 2, 3]


def test_surface_span_is_empty_when_an_edge_is_not_on_the_surface():
    """Empty means "cannot verify", so a caller cannot cite a span it does not cover.

    Both misses resolve the same way and for the same reason: the surface is live state,
    so an unknown session is not an empty session — it is one this process never emitted
    for. Returning a partial span in either case would let a producer cite a range it
    cannot actually see.
    """
    manager = _live_manager()
    for _ in range(3):
        asyncio.run(manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="s", surface_op=APPEND)))

    assert manager.surface_span("s", 0, 99) == []
    assert manager.surface_span("unknown-session", 0, 1) == []


def test_the_live_surface_keeps_a_malformed_replacement_rather_than_dropping_it():
    """The emit path is forgiving where the fold is strict.

    ``fold_surface`` reads a stored log and must refuse one it cannot interpret. Here the
    event is still in flight: refusing would lose the event itself, which is worse than
    a surface entry in the wrong place. It is appended instead, so the event survives and
    the stored log still records what its writer declared.
    """
    manager = _live_manager()
    asyncio.run(manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", surface_op=APPEND)))
    asyncio.run(manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s",
        surface_op=replace_op(50, 60))))          # names a range that does not exist

    assert manager.surface("s") == [0, 1]


# --------------------------------------------------------------------------- #
# Two readings, two consumers
# --------------------------------------------------------------------------- #
def test_a_compaction_removes_turns_from_the_model_history_and_not_from_the_transcript():
    """The distinction the two readers exist for, and the one that fails silently.

    `surface_events` is what the model is shown: a replacement shadows the range it stands
    for, which is the whole mechanism behind compaction. `transcript_events` is what a
    person is shown, and a person has already read those turns — rendering their view from
    the surface deletes conversation off the screen the moment a summary lands.

    Nothing catches that in a short session, because both readings agree exactly until the
    first compaction.
    """
    events = [_event(0), _event(1), _event(2),
              _event(3, op=replace_op(1, 2), cites=[1, 2])]

    model_history = [event.seq_no for event in surface_events(events)]
    human_transcript = [event.seq_no for event in transcript_events(events)]

    assert model_history == [0, 3]              # 1 and 2 are shadowed by the summary
    assert human_transcript == [0, 1, 2]        # and still on the reader's screen


def test_the_transcript_keeps_an_event_a_later_summary_shadowed():
    """Stated on its own because it is the property a fold would quietly destroy.

    Filtering transcript events by the folded surface is the obvious implementation and it
    reintroduces exactly the deletion being prevented.
    """
    events = [_event(0), _event(1), _event(2, op=replace_op(0, 1), cites=[0, 1])]

    assert [event.seq_no for event in transcript_events(events)] == [0, 1]


def test_the_transcript_excludes_the_summary_itself():
    """A summary is context assembled for the model, not something anyone said.

    Showing it in the conversation would put words on screen that no participant produced.
    """
    events = [_event(0), _event(1, op=replace_op(0, 0), cites=[0])]

    assert 1 not in [event.seq_no for event in transcript_events(events)]


def test_log_only_events_are_in_neither_reading():
    """A bookkeeping record never stood for history in either sense."""
    events = [_event(0), _event(1, op=None), _event(2)]

    assert [event.seq_no for event in transcript_events(events)] == [0, 2]
    assert [event.seq_no for event in surface_events(events)] == [0, 2]
