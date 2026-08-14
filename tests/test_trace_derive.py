"""The log can stand in for the rendered history — proven, not switched on.

`derive_messages` is not on the request path. These tests are what turn "could the
log be the source of the model's context?" from a guess into a fact, and they are the
gate the switch has to pass before anything is rewired.

The two readings of the log meet here: the surface decides which history events still
stand (a compaction summary shadows what it replaced), and log-only events supply the
call arguments that belong to an assistant turn rather than to a message of their own.
"""

import json

import pytest

from agentevolver.message import AssistantMessage, HumanMessage, ToolMessage
from agentevolver.trace.derive import derive_messages
from agentevolver.trace.surface import replace_op
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_call_event,
    agent_end_event,
    agent_start_event,
    tool_call_event,
    tool_start_event,
)


def _log(*events):
    """Stamp positions the way `trace_manager.emit` would."""
    for i, event in enumerate(events):
        event.seq_no = i
    return list(events)


def _turn(step, reasoning, calls):
    """One assistant step: its marker, the calls it made, and their results."""
    events = [agent_call_event("s", "t", "a", step, reasoning=reasoning)]
    for i, (name, args, result, ok) in enumerate(calls):
        events.append(tool_start_event("s", "t", "a", step, i, name, args, call_id=f"c{step}_{i}"))
        events.append(tool_call_event("s", "t", "a", step, i, name, result, ok, call_id=f"c{step}_{i}"))
    return events


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #
def test_a_task_becomes_the_opening_user_turn():
    messages = derive_messages(_log(agent_start_event("s", "t", "a", "fix the bug")))

    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].text == "fix the bug"


def test_a_step_becomes_an_assistant_turn_carrying_its_calls():
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "I will look at the file", [("read_file_tool", {"path": "a.py"}, "contents", True)]),
    ))

    assert [type(m) for m in messages] == [HumanMessage, AssistantMessage, ToolMessage]
    assistant = messages[1]
    assert assistant.text == "I will look at the file"
    assert [c.function.name for c in assistant.tool_calls] == ["read_file_tool"]
    assert json.loads(assistant.tool_calls[0].function.arguments) == {"path": "a.py"}


def test_a_result_answers_its_call_by_id():
    """Position pairing survives only while both ends survive in order; an id does not care."""
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "checking", [("bash_tool", {"command": "ls"}, "a.py", True)]),
    ))

    assistant, result = messages[1], messages[2]
    assert result.tool_call_id == assistant.tool_calls[0].id
    assert result.text == "a.py"
    assert result.is_error is False


def test_parallel_calls_in_one_step_stay_in_one_assistant_turn():
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "two at once", [
            ("read_file_tool", {"path": "a.py"}, "A", True),
            ("read_file_tool", {"path": "b.py"}, "B", True),
        ]),
    ))

    assert [type(m) for m in messages] == [HumanMessage, AssistantMessage, ToolMessage, ToolMessage]
    assert len(messages[1].tool_calls) == 2
    assert {m.tool_call_id for m in messages[2:]} == {c.id for c in messages[1].tool_calls}


def test_a_failed_call_is_marked_rather_than_described():
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "trying", [("bash_tool", {"command": "false"}, None, False)]),
    ))

    result = messages[-1]
    assert result.is_error is True


def test_the_final_result_closes_as_an_assistant_turn():
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        agent_end_event("s", "t", "a", True, "done: fixed"),
    ))

    assert isinstance(messages[-1], AssistantMessage)
    assert messages[-1].text == "done: fixed"


def test_a_multi_step_run_alternates_the_way_a_conversation_does():
    messages = derive_messages(_log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "look", [("read_file_tool", {"path": "a.py"}, "src", True)]),
        *_turn(2, "edit", [("edit_file_tool", {"path": "a.py"}, "ok", True)]),
        agent_end_event("s", "t", "a", True, "done"),
    ))

    assert [type(m) for m in messages] == [
        HumanMessage,
        AssistantMessage, ToolMessage,
        AssistantMessage, ToolMessage,
        AssistantMessage,
    ]


# --------------------------------------------------------------------------- #
# The surface decides what still stands
# --------------------------------------------------------------------------- #
def test_a_compaction_summary_replaces_what_it_shadowed():
    """The folded events stay in the log; the projection must not show them anyway."""
    events = _log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "step one", [("bash_tool", {"command": "a"}, "A", True)]),
        *_turn(2, "step two", [("bash_tool", {"command": "b"}, "B", True)]),
    )
    # seqs: 0 task | 1 step-1 turn, 2 start, 3 result | 4 step-2 turn, 5 start, 6 result.
    # The `*_start` events are log-only; everything else is on the surface and must be
    # cited, the assistant turns included.
    events.append(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", seq_no=len(events),
        message="Earlier: ran a and b.", metadata={"kind": "compaction"},
        surface_op=replace_op(0, 6), source_event_seqs=[0, 1, 3, 4, 6],
    ))

    messages = derive_messages(events)
    texts = [m.text for m in messages]

    assert texts == ["Earlier: ran a and b."]       # the summary is the whole history now
    assert "A" not in texts and "B" not in texts    # results shadowed
    assert "step one" not in texts                  # and so is the reasoning behind them


def test_a_summary_shadows_the_reasoning_as_well_as_the_results():
    """A summary must stand for the whole stretch, thinking included.

    While step markers were log-only the surface could not shadow them, so a compaction
    hid a result and left the reasoning that produced it in the history — a turn talking
    about work the model could no longer see.
    """
    events = _log(
        agent_start_event("s", "t", "a", "task"),
        *_turn(1, "step one", [("bash_tool", {"command": "a"}, "A", True)]),
    )
    events.append(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", seq_no=len(events),
        message="Earlier: ran a.", metadata={"kind": "compaction"},
        surface_op=replace_op(0, 3), source_event_seqs=[0, 1, 3],
    ))

    texts = [m.text for m in derive_messages(events)]
    assert texts == ["Earlier: ran a."]
    assert "step one" not in texts


def test_a_log_whose_surface_does_not_hold_up_is_refused():
    """Projecting a history the log does not support would be worse than stopping."""
    from agentevolver.trace.surface import SurfaceError

    events = _log(agent_start_event("s", "t", "a", "task"), agent_end_event("s", "t", "a", True, "d"))
    events.append(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s", seq_no=2,
        surface_op=replace_op(0, 1), source_event_seqs=[0],      # seq 1 uncited
    ))

    with pytest.raises(SurfaceError):
        derive_messages(events)


# --------------------------------------------------------------------------- #
# Older logs
# --------------------------------------------------------------------------- #
def test_a_log_written_before_call_ids_still_projects():
    """Those events have only (step, index); dropping their results would be worse."""
    events = _log(
        agent_start_event("s", "t", "a", "task"),
        agent_call_event("s", "t", "a", 1, reasoning="looking"),
        tool_start_event("s", "t", "a", 1, 0, "bash_tool", {"command": "ls"}),   # no call_id
        tool_call_event("s", "t", "a", 1, 0, "bash_tool", "a.py", True),        # no call_id
    )

    messages = derive_messages(events)
    assistant, result = messages[1], messages[2]
    assert result.tool_call_id == assistant.tool_calls[0].id
    assert result.text == "a.py"


def test_an_empty_log_projects_to_nothing():
    assert derive_messages([]) == []
