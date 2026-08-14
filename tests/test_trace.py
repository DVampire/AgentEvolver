"""Trace is a side channel, and a side channel that can break a run is worse than none.

Nothing the agent does depends on trace succeeding, which is exactly why every failure
mode here is about refusing to propagate. Emitting must not block the caller and must not
raise: an uninitialised manager, an event that cannot be serialised, a subscriber that
throws — each of those reaches the agent's own loop if it is allowed to, and takes down a
run over bookkeeping.

The other half is durability. Events are one JSONL file per session with an index beside
them, written by a consumer draining a queue, and both the file path and the index are
derived from a session id that arrives from outside. A rebind — the Gateway initializing
trace before any session exists, then binding one — has to move the writer's root and its
index together without dropping the events already in flight.
"""

import asyncio
import json
import os

import pytest

from agentevolver.queue import AsyncQueue
from agentevolver.trace.types import (
    EventConfidence,
    EventProvenance,
    TraceEvent,
    TraceEventType,
    agent_end_event,
    agent_start_event,
    compute_event_fingerprint,
    skill_call_event,
    tool_call_event,
    tool_start_event,
)
from agentevolver.trace.writer import TraceWriter

SESSION = "sess-1"
TASK = "task-1"
AGENT = "code_agent"


def an_event(**kw):
    kw.setdefault("event_type", TraceEventType.CUSTOM)
    kw.setdefault("session_id", SESSION)
    return TraceEvent(**kw)


@pytest.fixture
def writer(tmp_path):
    return TraceWriter(log_root=str(tmp_path / "trace"), queue=AsyncQueue())


async def drain(writer):
    """Run the writer until the queue is empty, then shut it down.

    Events are queued before ``start`` in most tests below; the writer is a consumer, so
    starting and immediately stopping drains everything already waiting.
    """
    writer.start()
    await writer.stop()


# --------------------------------------------------------------------------- #
# What an event is, before anyone writes it
# --------------------------------------------------------------------------- #
def test_an_event_defaults_to_a_live_high_confidence_record():
    """Provenance and confidence gate automated downstream actions.

    A replayed or synthetic event that defaulted to ``LIVE`` would be indistinguishable
    from a real one, so anything that acts on the log automatically would act on a
    rehearsal. Both fields have to be opt-out, not opt-in, because the majority of events
    are genuinely live and nothing sets them.
    """
    event = an_event()
    assert event.provenance is EventProvenance.LIVE
    assert event.confidence is EventConfidence.HIGH


def test_every_event_gets_its_own_id():
    assert an_event().id != an_event().id


def test_serialising_an_event_makes_the_timestamp_json_safe():
    """The whole log is JSONL, and a ``datetime`` is the one field that is not JSON.

    ``model_dump`` leaves it as a ``datetime`` object, so ``to_dict`` converts it. Without
    that the writer's ``json.dumps`` raises for every single event — and the writer
    swallows write errors, so the log would simply be empty.
    """
    dumped = an_event().to_dict()
    assert isinstance(dumped["timestamp"], str)
    json.dumps(dumped)  # must not raise


def test_the_fingerprint_is_stable_for_the_same_step():
    """Two records of the same step must collapse to one identity.

    The id is random per event, so it cannot answer "is this the same step seen twice".
    The fingerprint is derived from the step's coordinates instead, which is what lets a
    duplicate from a retry be recognised rather than counted again.
    """
    kw = dict(event_type=TraceEventType.TOOL_CALL, session_id=SESSION,
              step_number=1, action_index=0, action_name="bash")
    assert compute_event_fingerprint(TraceEvent(**kw)) == compute_event_fingerprint(TraceEvent(**kw))


@pytest.mark.parametrize("field, value", [
    ("step_number", 2), ("action_index", 1), ("action_name", "python"), ("session_id", "other"),
])
def test_the_fingerprint_changes_when_the_step_identity_does(field, value):
    """Each coordinate is checked separately because one left out of the hash is silent.

    A fingerprint that ignored ``action_index`` would fuse the parallel actions of a single
    step into one identity — they share everything else — and deduplication would then
    discard real work.
    """
    kw = dict(event_type=TraceEventType.TOOL_CALL, session_id=SESSION,
              step_number=1, action_index=0, action_name="bash")
    baseline = compute_event_fingerprint(TraceEvent(**kw))
    assert compute_event_fingerprint(TraceEvent(**{**kw, field: value})) != baseline


# --------------------------------------------------------------------------- #
# What the constructors put in an event
# --------------------------------------------------------------------------- #
def test_a_start_event_carries_the_task_it_was_given():
    """The task text is the only record of what the run was asked to do.

    It goes in ``input`` rather than being folded into the label, so a reader can recover
    the request verbatim rather than parsing it back out of a display string.
    """
    event = agent_start_event(SESSION, TASK, AGENT, "Fix the bug")
    assert event.event_type is TraceEventType.AGENT_START
    assert event.input == {"task": "Fix the bug"}
    assert AGENT in event.label


@pytest.mark.parametrize("success, marker", [(True, "ok"), (False, "fail")])
def test_an_end_event_says_in_its_label_whether_it_worked(success, marker):
    """The outcome is recorded three ways, and all three are read by something different.

    The label is what a human scanning the UI sees, ``success`` is what code branches on,
    and ``metadata`` is what survives into aggregate summaries. Setting only one leaves the
    other two claiming the opposite.
    """
    event = agent_end_event(SESSION, TASK, AGENT, success, "result")
    assert marker in event.label
    assert event.success is success
    assert event.metadata["success"] is success


def test_a_failed_end_event_keeps_the_error():
    """A failure has an error and no result — the two fields must not be conflated.

    ``message`` stays ``None`` rather than becoming ``"None"``: the constructor stringifies
    the result, and a result that was never produced has to stay absent so a reader can
    tell "failed with nothing to show" from "returned the string None".
    """
    event = agent_end_event(SESSION, TASK, AGENT, False, None, error="boom")
    assert event.error == "boom"
    assert event.message is None


def test_tool_and_skill_events_are_distinguishable_by_action_type():
    """The two constructors are near-identical, which is how they end up mislabelled.

    Everything downstream that counts or renders actions splits on ``action_type``; a
    skill recorded as a tool is not visibly wrong anywhere, it just makes the totals lie.
    """
    tool = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "out", True)
    skill = skill_call_event(SESSION, TASK, AGENT, 0, 0, "review", "out", True)
    assert tool.action_type == "tool"
    assert skill.action_type == "skill"


def test_a_start_event_records_the_arguments_the_action_was_called_with():
    """Without the arguments the log says a tool ran, not what it was asked to do.

    That is the difference between a trace someone can debug from and one that only
    confirms something happened.
    """
    event = tool_start_event(SESSION, TASK, AGENT, 0, 0, "bash", {"cmd": "ls"})
    assert event.input == {"cmd": "ls"}


def test_an_optional_description_reaches_the_metadata():
    """Present when given, and the key absent when not — not present-and-empty.

    An always-present ``description: ""`` would make "no description" and "description was
    blank" the same, and every consumer would have to guard for a key that carries no
    information.
    """
    with_it = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "o", True, description="list files")
    without = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "o", True)
    assert with_it.metadata["description"] == "list files"
    assert "description" not in without.metadata


def test_a_non_string_result_is_still_rendered_as_a_message():
    """Tools return whatever they like; the log needs both the value and a displayable form.

    ``output`` keeps the structure for anything that reads the log as data, ``message``
    carries the rendered form for anything that shows it. Keeping only one costs either
    the fidelity or the display.
    """
    event = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", {"files": 3}, True)
    assert event.output == {"files": 3}
    assert event.message == "{'files': 3}"


# --------------------------------------------------------------------------- #
# Getting events onto disk
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_events_are_appended_one_json_object_per_line(writer):
    """JSONL, in the order emitted — the format the whole read path assumes.

    Order is the assertion that matters: the writer holds an open handle per session and
    appends, so anything that buffers or re-opens can reorder events, and a log whose
    events are out of order describes a run that did not happen.
    """
    for n in range(3):
        writer._queue.emit(an_event(label=f"e{n}"))
    await drain(writer)
    assert [e["label"] for e in writer.read_session(SESSION)] == ["e0", "e1", "e2"]


@pytest.mark.asyncio
async def test_each_session_gets_its_own_file(writer):
    """One queue feeds many sessions; the split happens at write time.

    A single shared file would still be readable but would force every reader of one
    session to scan every other session's events, and the Gateway reads these per session.
    """
    writer._queue.emit(an_event(session_id="a"))
    writer._queue.emit(an_event(session_id="b"))
    await drain(writer)
    assert len(writer.read_session("a")) == 1
    assert len(writer.read_session("b")) == 1


@pytest.mark.asyncio
async def test_an_event_without_a_session_still_lands_somewhere(writer):
    """Losing an event because nobody set a session id would be a silent gap.

    Errors are the events most likely to be raised outside a session's scope, and they are
    the ones it would hurt most to drop. They go to a fixed fallback file instead.
    """
    writer._queue.emit(TraceEvent(event_type=TraceEventType.ERROR))
    await drain(writer)
    assert len(writer.read_session("no_session")) == 1


@pytest.mark.asyncio
async def test_a_session_id_with_slashes_cannot_escape_the_trace_root(writer):
    """The session id comes from a caller and is used to build a filename.

    Left as-is, it is a path fragment: the separators are what make traversal possible at
    all, so they are replaced before the join rather than checked after it.
    """
    assert "/" not in os.path.basename(writer._session_path("a/../../b"))


@pytest.mark.asyncio
async def test_the_index_summarises_what_each_session_contains(writer):
    """The index is what the session list is built from, without opening any log file.

    The two events deliberately carry different agents and task ids, because the summary
    accumulates sets across a session rather than recording the last value seen — a
    reduction that looks right on a single-agent run and loses the others as soon as one
    session involves a second agent.
    """
    writer._queue.emit(agent_start_event(SESSION, TASK, AGENT, "t"))
    writer._queue.emit(agent_end_event(SESSION, "task-2", "reviewer", True, "done"))
    await drain(writer)

    index = json.loads(open(writer._index_path).read())
    session = index["sessions"][0]
    assert session["event_count"] == 2
    assert sorted(session["agent_names"]) == ["code_agent", "reviewer"]
    assert sorted(session["task_ids"]) == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_reading_a_session_that_was_never_written_is_empty(writer):
    """A session with no file is a normal read, not a missing-file error.

    The Gateway asks for sessions it has not seen produce events yet; raising here would
    turn an ordinary poll into a failure.
    """
    assert writer.read_session("never") == []


@pytest.mark.asyncio
async def test_one_corrupt_line_does_not_hide_the_rest(writer):
    """A log truncated mid-write must still give up everything written before it.

    A process killed while appending leaves exactly this: a partial final line. Parsing the
    file as one document, or stopping at the first bad line, discards a whole session's
    history to protect against its last few bytes.
    """
    writer._queue.emit(an_event(label="good"))
    await drain(writer)
    with open(writer._session_path(SESSION), "a", encoding="utf-8") as fh:
        fh.write("{ broken\n\n")
    assert [e["label"] for e in writer.read_session(SESSION)] == ["good"]


@pytest.mark.asyncio
async def test_an_event_that_cannot_be_written_does_not_stop_the_loop(writer):
    """One bad event must not take the whole trace subsystem down.

    The consumer is a single loop over the queue: an exception that escapes it ends
    tracing for the rest of the process, and nothing restarts it. So the bad event is
    dropped and the loop continues — "before" is missing from the log and "after" is not.
    """
    class Unserialisable:
        # Raising from __repr__ is what makes the event unwritable: json.dumps builds its
        # own error message from the object, so even the failure path cannot render it.
        def __repr__(self):
            raise RuntimeError("nope")

    writer._queue.emit(an_event(label="before", output=Unserialisable()))
    writer._queue.emit(an_event(label="after"))
    await drain(writer)
    assert [e["label"] for e in writer.read_session(SESSION)] == ["after"]


# --------------------------------------------------------------------------- #
# Moving the log root under a running writer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rebinding_moves_later_events_under_the_new_root(writer, tmp_path):
    """The Gateway initializes trace before any session exists, then binds one.

    So the writer is already running, with an open handle, when its destination changes.
    The handle has to be closed and the index moved with it: an index left behind at the
    old root describes sessions whose files are somewhere else. The sleep lets the consumer
    actually write "before" under the original root, so the rebind is a live one rather
    than a reconfiguration of an idle writer.
    """
    writer._queue.emit(an_event(label="before"))
    writer.start()
    await asyncio.sleep(0.05)

    new_root = str(tmp_path / "session-log" / "trace")
    writer.rebind(new_root)
    writer._queue.emit(an_event(label="after"))
    await writer.stop()

    assert [e["label"] for e in writer.read_session(SESSION)] == ["after"]
    assert writer._index_path == os.path.join(new_root, "index.json")


def test_rebinding_to_the_same_root_changes_nothing(writer):
    """Rebinding is called on every session bind, often to the root already in use.

    Treating that as a real move would close the open handles and clear the accumulated
    per-session summaries, so the index would restart its counts from zero mid-session.
    The count of 7 stands in for a session already well underway.
    """
    writer._session_meta[SESSION] = {"event_count": 7}
    writer.rebind(writer._log_root)
    assert writer._session_meta[SESSION]["event_count"] == 7


# --------------------------------------------------------------------------- #
# The manager's refusal to propagate a failure
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_emitting_before_start_is_dropped_rather_than_queued(tmp_path):
    """Trace is a side channel; an uninitialised one must not raise into a run.

    Emit calls are scattered through the agent loop, and some of them run before trace is
    up — during startup, or in a test that never initializes it. Buffering instead would
    hold events for a queue that may never exist.
    """
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    await manager.emit(an_event())  # must not raise


@pytest.mark.asyncio
async def test_starting_before_initialize_is_refused():
    """The one place trace *should* raise: a caller misordering its own lifecycle.

    This is not the agent's hot path — it is setup code, and failing loudly here is what
    stops a host from running with a manager that has no queue and silently records
    nothing for the rest of its life.
    """
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    with pytest.raises(RuntimeError, match="initialize"):
        await manager.start()


@pytest.mark.asyncio
async def test_subscribers_receive_events_and_a_broken_one_is_survived(tmp_path):
    """Fan-out is how the Gateway streams to the UI, and a subscriber it does not own.

    The broken callback is registered first deliberately: a loop that lets an exception
    escape stops before it reaches the other two, so the failure of one consumer silently
    unsubscribes everyone after it. Both a sync and an async callback are checked because
    the loop awaits only what is awaitable, and getting that wrong drops one kind
    entirely. Unsubscribe is checked in the same test to confirm the set is the live
    registry, not a snapshot taken at start.
    """
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    await manager.initialize(log_root=str(tmp_path / "trace"))
    await manager.start()

    seen = []

    def broken(event):
        raise RuntimeError("subscriber exploded")

    async def async_ok(event):
        seen.append(("async", event.label))

    def sync_ok(event):
        seen.append(("sync", event.label))

    for callback in (broken, async_ok, sync_ok):
        manager.subscribe(callback)
    await manager.emit(an_event(label="e"))

    assert sorted(seen) == [("async", "e"), ("sync", "e")]

    manager.unsubscribe(async_ok)
    await manager.emit(an_event(label="e2"))
    assert ("async", "e2") not in seen
    await manager.stop()
