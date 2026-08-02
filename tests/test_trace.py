"""Trace: the observability path, and the two ways it must refuse to break a run.

Trace is a side channel — the agent's work does not depend on it, so emitting
must never block the caller and never raise, no matter what a subscriber does.
The other half is durability: events are one JSONL file per session with an
index beside them, and a rebind (the Gateway binding a session after startup)
has to move both without losing the handle it already had open.
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
    """Run the writer until the queue is empty, then shut it down."""
    writer.start()
    await writer.stop()


# ------------------------------------------------------------------- events
def test_an_event_defaults_to_a_live_high_confidence_record():
    """Provenance and confidence gate automated downstream actions."""
    event = an_event()
    assert event.provenance is EventProvenance.LIVE
    assert event.confidence is EventConfidence.HIGH


def test_every_event_gets_its_own_id():
    assert an_event().id != an_event().id


def test_serialising_an_event_makes_the_timestamp_json_safe():
    dumped = an_event().to_dict()
    assert isinstance(dumped["timestamp"], str)
    json.dumps(dumped)  # must not raise


def test_the_fingerprint_is_stable_for_the_same_step():
    """Two records of the same step must collapse to one identity."""
    kw = dict(event_type=TraceEventType.TOOL_CALL, session_id=SESSION,
              step_number=1, action_index=0, action_name="bash")
    assert compute_event_fingerprint(TraceEvent(**kw)) == compute_event_fingerprint(TraceEvent(**kw))


@pytest.mark.parametrize("field, value", [
    ("step_number", 2), ("action_index", 1), ("action_name", "python"), ("session_id", "other"),
])
def test_the_fingerprint_changes_when_the_step_identity_does(field, value):
    kw = dict(event_type=TraceEventType.TOOL_CALL, session_id=SESSION,
              step_number=1, action_index=0, action_name="bash")
    baseline = compute_event_fingerprint(TraceEvent(**kw))
    assert compute_event_fingerprint(TraceEvent(**{**kw, field: value})) != baseline


# ------------------------------------------------------------ constructors
def test_a_start_event_carries_the_task_it_was_given():
    event = agent_start_event(SESSION, TASK, AGENT, "Fix the bug")
    assert event.event_type is TraceEventType.AGENT_START
    assert event.input == {"task": "Fix the bug"}
    assert AGENT in event.label


@pytest.mark.parametrize("success, marker", [(True, "ok"), (False, "fail")])
def test_an_end_event_says_in_its_label_whether_it_worked(success, marker):
    event = agent_end_event(SESSION, TASK, AGENT, success, "result")
    assert marker in event.label
    assert event.success is success
    assert event.metadata["success"] is success


def test_a_failed_end_event_keeps_the_error():
    event = agent_end_event(SESSION, TASK, AGENT, False, None, error="boom")
    assert event.error == "boom"
    assert event.message is None


def test_tool_and_skill_events_are_distinguishable_by_action_type():
    tool = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "out", True)
    skill = skill_call_event(SESSION, TASK, AGENT, 0, 0, "review", "out", True)
    assert tool.action_type == "tool"
    assert skill.action_type == "skill"


def test_a_start_event_records_the_arguments_the_action_was_called_with():
    event = tool_start_event(SESSION, TASK, AGENT, 0, 0, "bash", {"cmd": "ls"})
    assert event.input == {"cmd": "ls"}


def test_an_optional_description_reaches_the_metadata():
    with_it = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "o", True, description="list files")
    without = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", "o", True)
    assert with_it.metadata["description"] == "list files"
    assert "description" not in without.metadata


def test_a_non_string_result_is_still_rendered_as_a_message():
    event = tool_call_event(SESSION, TASK, AGENT, 0, 0, "bash", {"files": 3}, True)
    assert event.output == {"files": 3}
    assert event.message == "{'files': 3}"


# -------------------------------------------------------------------- writer
@pytest.mark.asyncio
async def test_events_are_appended_one_json_object_per_line(writer):
    for n in range(3):
        writer._queue.emit(an_event(label=f"e{n}"))
    await drain(writer)
    assert [e["label"] for e in writer.read_session(SESSION)] == ["e0", "e1", "e2"]


@pytest.mark.asyncio
async def test_each_session_gets_its_own_file(writer):
    writer._queue.emit(an_event(session_id="a"))
    writer._queue.emit(an_event(session_id="b"))
    await drain(writer)
    assert len(writer.read_session("a")) == 1
    assert len(writer.read_session("b")) == 1


@pytest.mark.asyncio
async def test_an_event_without_a_session_still_lands_somewhere(writer):
    """Losing an event because nobody set a session id would be a silent gap."""
    writer._queue.emit(TraceEvent(event_type=TraceEventType.ERROR))
    await drain(writer)
    assert len(writer.read_session("no_session")) == 1


@pytest.mark.asyncio
async def test_a_session_id_with_slashes_cannot_escape_the_trace_root(writer):
    assert "/" not in os.path.basename(writer._session_path("a/../../b"))


@pytest.mark.asyncio
async def test_the_index_summarises_what_each_session_contains(writer):
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
    assert writer.read_session("never") == []


@pytest.mark.asyncio
async def test_one_corrupt_line_does_not_hide_the_rest(writer):
    writer._queue.emit(an_event(label="good"))
    await drain(writer)
    with open(writer._session_path(SESSION), "a", encoding="utf-8") as fh:
        fh.write("{ broken\n\n")
    assert [e["label"] for e in writer.read_session(SESSION)] == ["good"]


@pytest.mark.asyncio
async def test_an_event_that_cannot_be_written_does_not_stop_the_loop(writer):
    """One bad event must not take the whole trace subsystem down."""
    class Unserialisable:
        def __repr__(self):
            raise RuntimeError("nope")

    writer._queue.emit(an_event(label="before", output=Unserialisable()))
    writer._queue.emit(an_event(label="after"))
    await drain(writer)
    assert [e["label"] for e in writer.read_session(SESSION)] == ["after"]


# -------------------------------------------------------------------- rebind
@pytest.mark.asyncio
async def test_rebinding_moves_later_events_under_the_new_root(writer, tmp_path):
    """The Gateway initializes trace before any session exists, then binds one."""
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
    writer._session_meta[SESSION] = {"event_count": 7}
    writer.rebind(writer._log_root)
    assert writer._session_meta[SESSION]["event_count"] == 7


# ------------------------------------------------------------------- manager
@pytest.mark.asyncio
async def test_emitting_before_start_is_dropped_rather_than_queued(tmp_path):
    """Trace is a side channel; an uninitialised one must not raise into a run."""
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    await manager.emit(an_event())  # must not raise


@pytest.mark.asyncio
async def test_starting_before_initialize_is_refused():
    from agentevolver.trace.server import TraceManager

    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    with pytest.raises(RuntimeError, match="initialize"):
        await manager.start()


@pytest.mark.asyncio
async def test_subscribers_receive_events_and_a_broken_one_is_survived(tmp_path):
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
