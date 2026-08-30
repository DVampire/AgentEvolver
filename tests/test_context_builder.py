import asyncio

from agentevolver.agent.context_builder import ContextBuilder, strip_rendered_comments
from agentevolver.hook.default.trace import TraceHook
from agentevolver.hook.types import HookContext, HookEvent
from agentevolver.message import (
    AssistantMessage,
    CompactionMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
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
        provider_state={
            "responses": {
                "compaction_items": [
                    {"type": "compaction", "encrypted_content": "opaque"}
                ]
            }
        },
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
        SystemMessage, HumanMessage, CompactionMessage, AssistantMessage, ToolMessage,
        HumanMessage,
    ]
    assert messages[1].cache is True
    assert "fix the bug" in messages[1].text
    assert "Found the faulty branch." in messages[2].text
    assert messages[2].provider_state["responses"]["compaction_items"][0]["type"] == "compaction"
    assert "old" not in "\n".join(message.text for message in messages)
    assert messages[3].tool_calls[0].id == messages[4].tool_call_id == "c2"
    assert messages[3].cache is True
    assert messages[4].cache is False
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


def test_compaction_drops_old_provider_state_and_keeps_the_exact_recent_turn():
    old = {"responses": {"reasoning_items": [{"type": "reasoning", "id": "old"}]}}
    recent = {"responses": {"reasoning_items": [{"type": "reasoning", "id": "recent"}]}}
    events = _number([
        agent_start_event("s", "t", "a", "task"),
        agent_call_event("s", "t", "a", 1, provider_state=old),
    ])
    events.append(TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        seq_no=2,
        message="Old step completed.",
        metadata={"type": "compaction"},
        surface_op=replace_op(0, 1),
        source_event_seqs=[0, 1],
    ))
    events.append(agent_call_event("s", "t", "a", 2, provider_state=recent))
    events[-1].seq_no = 3
    rendered = [SystemMessage(content="rules"), HumanMessage(content="<task>task</task>")]

    messages = ContextBuilder().build(rendered, events, type("C", (), {"extra": {}})())
    states = [
        message.provider_state for message in messages
        if isinstance(message, AssistantMessage) and message.provider_state
    ]

    assert states == [recent]


def test_template_comments_are_not_sent_but_task_comments_remain():
    events = _number([
        agent_start_event("s", "t", "a", "fix <!-- meaningful fixture -->"),
    ])
    rendered = [
        SystemMessage(content="rules <!-- maintainer note -->"),
        HumanMessage(content=(
            "<!-- module documentation -->"
            "<agent-context><task>stale</task>"
            "<constraints>10 left</constraints></agent-context>"
        )),
    ]

    messages = ContextBuilder().build(rendered, events, type("C", (), {"extra": {}})())

    assert "maintainer note" not in messages[0].text
    assert "module documentation" not in messages[-1].text
    assert "<!-- meaningful fixture -->" in messages[1].text


def test_first_request_strips_template_comments_without_losing_task_comments():
    rendered = [
        SystemMessage(content="rules <!-- maintainer note -->"),
        HumanMessage(content=(
            "<!-- module documentation -->"
            "<agent-context><task>fix <!-- task fixture --> it</task>"
            "<constraints>10 left</constraints></agent-context>"
        )),
    ]

    messages = ContextBuilder().build(rendered, [], type("C", (), {"extra": {}})())

    assert "maintainer note" not in messages[0].text
    assert "module documentation" not in messages[-1].text
    assert "<!-- task fixture -->" in messages[1].text
    assert messages[1].cache is True


def test_rendered_fallback_preserves_comments_inside_memory_payloads():
    rendered = [HumanMessage(content=(
        "<!-- template note -->"
        "<working-memory>code has <!-- meaningful --> marker</working-memory>"
    ))]

    cleaned = strip_rendered_comments(rendered)[0].text

    assert "template note" not in cleaned
    assert "<!-- meaningful -->" in cleaned


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
