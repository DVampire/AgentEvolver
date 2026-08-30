import asyncio

from agentevolver.agent.context_builder import ContextBuilder
from agentevolver.hook.default.trace import TraceHook
from agentevolver.hook.types import HookContext, HookEvent
from agentevolver.message import AssistantMessage, HumanMessage, SystemMessage, ToolMessage
from agentevolver.trace.surface import replace_op
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_call_event,
    agent_start_event,
    tool_call_event,
    tool_start_event,
)


def _number(events):
    for seq, event in enumerate(events):
        event.seq_no = seq
    return events


def test_builder_keeps_anchor_checkpoint_recent_turns_and_live_tail_separate():
    events = _number([
        agent_start_event("s", "t", "a", "fix the bug"),
        tool_start_event("s", "t", "a", 1, 0, "bash_tool", {"command": "cat a"}, "c1"),
        tool_call_event("s", "t", "a", 1, 0, "bash_tool", "old", True, call_id="c1"),
        agent_call_event("s", "t", "a", 1, "inspect"),
    ])
    events.append(TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        seq_no=4,
        message="Found the faulty branch.",
        metadata={"type": "compaction"},
        surface_op=replace_op(0, 3),
        source_event_seqs=[0, 2, 3],
    ))
    events.extend(_number([
        tool_start_event("s", "t", "a", 2, 0, "bash_tool", {"command": "git diff"}, "c2"),
        tool_call_event("s", "t", "a", 2, 0, "bash_tool", "diff", True, call_id="c2"),
        agent_call_event("s", "t", "a", 2, "verify"),
    ]))
    for seq, event in enumerate(events):
        event.seq_no = seq

    rendered = [
        SystemMessage(content="rules"),
        HumanMessage(content=(
            "<capability-context><tool-context>bash</tool-context></capability-context>"
            "<agent-context><task>duplicate</task><constraints>10 left</constraints>"
            "<recent-steps>duplicate</recent-steps></agent-context>"
        )),
    ]
    messages = ContextBuilder().build(rendered, events, type("C", (), {"extra": {}})())

    assert [type(message) for message in messages] == [
        SystemMessage, HumanMessage, HumanMessage, AssistantMessage, ToolMessage,
        HumanMessage,
    ]
    assert messages[1].cache is True
    assert "fix the bug" in messages[1].text
    assert "Found the faulty branch." in messages[2].text
    assert "old" not in "\n".join(message.text for message in messages)
    assert messages[3].tool_calls[0].id == messages[4].tool_call_id == "c2"
    assert messages[4].cache is True
    assert "<constraints>10 left</constraints>" in messages[-1].text
    assert "capability-context" not in "\n".join(message.text for message in messages)


def test_private_reasoning_is_not_replayed_as_assistant_text():
    event = agent_call_event("s", "t", "a", 1, reasoning="hidden chain", assistant_text="")
    events = _number([
        agent_start_event("s", "t", "a", "task"),
        event,
    ])
    rendered = [SystemMessage(content="rules"), HumanMessage(content="<task>task</task>")]

    messages = ContextBuilder().build(rendered, events, type("C", (), {"extra": {}})())

    assert all("hidden chain" not in message.text for message in messages)


def test_provider_replay_state_survives_trace_projection():
    state = {"responses": {"reasoning_items": [{"type": "reasoning", "id": "r1"}]}}
    events = _number([
        agent_start_event("s", "t", "a", "task"),
        agent_call_event("s", "t", "a", 1, provider_state=state),
    ])
    rendered = [SystemMessage(content="rules"), HumanMessage(content="<task>task</task>")]

    messages = ContextBuilder().build(rendered, events, type("C", (), {"extra": {}})())

    assistant = next(message for message in messages if isinstance(message, AssistantMessage))
    assert assistant.provider_state == state


def test_trace_hook_hands_memory_the_same_numbered_event(monkeypatch):
    from agentevolver.memory import memory_manager
    from agentevolver.trace import trace_manager

    seen = []

    async def emit(event):
        event.seq_no = 17
        seen.append(("trace", event))
        return True

    async def consume(event, **kwargs):
        seen.append(("memory", event, kwargs))

    monkeypatch.setattr(trace_manager, "emit", emit)
    monkeypatch.setattr(memory_manager, "consume_trace_event", consume)
    asyncio.run(TraceHook().handle(HookContext(
        id="s",
        name="trace_hook",
        input={
            "event": HookEvent.POST_ACTION,
            "agent_name": "a",
            "task_id": "t",
            "step_number": 1,
            "action": {"type": "tool", "name": "bash_tool", "id": "c1"},
            "action_result": "ok",
            "use_memory": True,
            "memory_name": "file_system_memory",
        },
    )))

    assert seen[0][1] is seen[1][1]
    assert seen[1][1].seq_no == 17
    assert seen[1][2] == {"memory_name": "file_system_memory", "enabled": True}


def test_bash_effect_prediction_distinguishes_reads_from_edits():
    from agentevolver.tool.default.bash import BashTool

    tool = BashTool()
    assert tool.will_mutate({"command": "cat src/a.py | grep uid"}) is False
    assert tool.will_mutate({"command": "sed -n '1,80p' src/a.py"}) is False
    assert tool.will_mutate({"command": "sed -i 's/a/b/' src/a.py"}) is True
    assert tool.will_mutate({"command": "python fix.py"}) is None
