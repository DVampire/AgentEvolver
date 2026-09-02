"""The context window: four layers, where the cache may be cut, and when history folds.

Layout is a cost decision, not a formatting one. A provider's prompt cache is a prefix
match, so a breakpoint in the wrong place makes a session pay for its own history on
every step — and that failure is invisible, because the request is still correct. Each
test here pins one placement rule against exactly that.
"""

import pytest

from agentevolver.agent.context import (
    ContextAssembler,
    ContextEnvelope,
    ContextProtocolError,
    Conversation,
)
from agentevolver.message.types import (
    AssistantMessage,
    Function,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)

_ids = iter(range(1, 10**6))

ANTHROPIC_NATIVE = {"anthropic": {"compaction_blocks": [{"type": "compaction"}]}}
RESPONSES_NATIVE = {"responses": {"compaction_items": [{"type": "compaction"}]}}


def turn(conversation: Conversation, bulk: int = 2) -> None:
    index = next(_ids)
    call_id = f"c{index}"
    conversation.add_turn(
        AssistantMessage(content=f"step {index}", tool_calls=[ToolCall(
            id=call_id,
            function=Function(name="read_file", arguments=f'{{"path":"f{index}.py"}}'),
        )]),
        [ToolMessage(
            content=f"line {index}\n" * bulk, tool_call_id=call_id, name="read_file",
        )],
    )


def conversation(turns: int = 6, bulk: int = 2) -> Conversation:
    held = Conversation(task="fix the parser")
    held.set_system([SystemMessage(content="You are a coding agent.")])
    for _ in range(turns):
        turn(held, bulk)
    return held


def layer(messages, name):
    return [message for message in messages if message.context_layer == name]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_the_layers_come_out_in_order_with_the_volatile_block_last():
    held = conversation()
    messages = ContextAssembler().build(held, live=["<budget>2 steps left</budget>"])
    order = ["fixed", "checkpoint", "recent", "live"]
    positions = [order.index(message.context_layer) for message in messages]

    assert positions == sorted(positions)
    assert messages[0].role == "system"
    assert messages[-1].context_layer == "live"
    assert messages[-1].cache is False


def test_the_volatile_state_is_one_message_however_many_blocks_it_carries():
    """Each is a cache miss by construction, and the model reads them as one situation."""
    messages = ContextAssembler().build(
        conversation(), live=["<budget>a</budget>", "<errors>b</errors>", ""],
    )
    live = layer(messages, "live")
    assert len(live) == 1
    assert "<budget>a</budget>" in live[0].text
    assert "<errors>b</errors>" in live[0].text


def test_no_live_block_means_no_live_message():
    messages = ContextAssembler().build(conversation())
    assert layer(messages, "live") == []


def test_images_ride_in_the_live_layer_so_they_survive_more_than_one_step():
    held = conversation()
    picture = HumanMessage(content="[screenshot]")
    messages = ContextAssembler().build(held, live=["<budget>x</budget>"], attachments=[picture])
    live = layer(messages, "live")
    assert len(live) == 2
    assert live[-1].cache is False


# ---------------------------------------------------------------------------
# Cache placement
# ---------------------------------------------------------------------------


def test_without_a_checkpoint_the_anchor_and_the_last_turn_are_the_breakpoints():
    messages = ContextAssembler().build(conversation())
    marked = [(message.context_layer, message.role) for message in messages if message.cache]
    assert marked == [("fixed", "user"), ("recent", "assistant")]


def test_a_text_checkpoint_leaves_the_anchor_breakpoint_where_it_was():
    held = conversation()
    held.fold("read five files", keep_turns=2)
    messages = ContextAssembler().build(held)

    assert layer(messages, "fixed")[-1].cache is True
    assert layer(messages, "checkpoint")[0].cache is False


def test_an_anthropic_native_block_becomes_the_prefix_and_takes_the_breakpoint():
    """The provider's own compaction replaced the history, so caching the old anchor
    would ask it to cache a prefix it has already superseded."""
    held = conversation()
    held.fold("read five files", keep_turns=2, provider_state=ANTHROPIC_NATIVE)
    messages = ContextAssembler().build(held)

    assert layer(messages, "fixed")[-1].cache is False
    assert layer(messages, "checkpoint")[0].cache is True


def test_a_responses_native_item_does_not_move_the_breakpoint():
    held = conversation()
    held.fold("read five files", keep_turns=2, provider_state=RESPONSES_NATIVE)
    messages = ContextAssembler().build(held)
    assert layer(messages, "fixed")[-1].cache is True


def test_marking_breakpoints_never_mutates_the_held_conversation():
    held = conversation()
    held.fold("summary", keep_turns=2, provider_state=ANTHROPIC_NATIVE)
    ContextAssembler().build(held)
    assert held.checkpoint.cache is False
    assert all(message.cache is False for message in held.items)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


def test_a_turn_whose_results_never_arrived_is_refused():
    held = conversation(turns=1)
    held.append(AssistantMessage(content="pending", tool_calls=[ToolCall(
        id="dangling", function=Function(name="read_file", arguments="{}"),
    )]))
    assert held.complete is False
    with pytest.raises(ContextProtocolError):
        ContextAssembler().build(held)


def test_a_reused_tool_call_id_is_refused():
    held = conversation(turns=0)
    for _ in range(2):
        held.add_turn(
            AssistantMessage(content="x", tool_calls=[ToolCall(
                id="same", function=Function(name="read_file", arguments="{}"),
            )]),
            [ToolMessage(content="y", tool_call_id="same", name="read_file")],
        )
    with pytest.raises(ContextProtocolError):
        ContextAssembler().build(held)


def test_a_compaction_message_outside_the_checkpoint_layer_is_refused():
    held = conversation()
    held.fold("summary", keep_turns=2)
    with pytest.raises(ContextProtocolError):
        ContextEnvelope(recent=(held.checkpoint,)).validate()


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------


def test_each_fold_signal_fires_on_its_own():
    by_turns = ContextAssembler(retain_turns=2, compact_after_turns=5,
                                compact_body_tokens=0, fold_at_pressure=0)
    by_body = ContextAssembler(retain_turns=2, compact_after_turns=0,
                               compact_body_tokens=2_000, fold_at_pressure=0)
    by_pressure = ContextAssembler(retain_turns=2, compact_after_turns=0,
                                   compact_body_tokens=0, fold_at_pressure=0.5,
                                   context_window=2_000)
    small, large = conversation(turns=6, bulk=1), conversation(turns=6, bulk=200)

    assert "turns" in by_turns.fold_reason(small)
    assert by_body.fold_reason(small) == ""
    assert "body" in by_body.fold_reason(large)
    assert "capacity" in by_pressure.fold_reason(large)


def test_folding_waits_for_a_complete_turn():
    """Cutting across an unanswered call would sever it from its result."""
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2)
    held = conversation(turns=6)
    assert assembler.fold_reason(held) != ""
    held.append(AssistantMessage(content="pending", tool_calls=[ToolCall(
        id="dangling", function=Function(name="read_file", arguments="{}"),
    )]))
    assert assembler.fold_reason(held) == ""


def test_the_fold_budget_and_the_retained_tail_both_stop_folding():
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2, max_folds=3)
    held = conversation(turns=6)
    assert assembler.fold_reason(held, folds=3) == ""
    assert ContextAssembler(retain_turns=99).fold_reason(held) == ""


def test_a_fold_cuts_at_a_turn_boundary_and_keeps_the_tail_sendable():
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2)
    held = conversation(turns=6)
    before = len(held.items)

    assert assembler.fold(held, "read six files; nothing changed yet") > 0
    assert held.turns == 2
    assert len(held.items) < before
    assert held.complete
    # The kept tail opens with an assistant turn, never an orphan result.
    assert isinstance(held.items[0], AssistantMessage)
    assembler.build(held)


def test_a_second_fold_merges_into_the_one_canonical_checkpoint():
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2)
    held = conversation(turns=6)
    assembler.fold(held, "first pass")
    for _ in range(4):
        turn(held)
    assembler.fold(held, "second pass")

    envelope = assembler.build_envelope(held)
    assert len(envelope.checkpoint) == 1
    assert "first pass" in envelope.checkpoint[0].text
    assert "second pass" in envelope.checkpoint[0].text


def test_a_checkpoint_that_saves_nothing_is_refused():
    """A summariser can expand, and the result would replace turns nothing can recover."""
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2)
    held = conversation(turns=6, bulk=1)
    bloated = "x " * 5_000

    assert assembler.fold(held, bloated) == 0
    assert held.checkpoint is None
    assert assembler.fold(held, "") == 0


def test_a_native_checkpoint_is_trusted_without_the_size_check():
    assembler = ContextAssembler(retain_turns=2, compact_after_turns=2)
    held = conversation(turns=6, bulk=1)
    assert assembler.fold(held, "", provider_state=ANTHROPIC_NATIVE) > 0
    assert held.checkpoint.provider_state == ANTHROPIC_NATIVE


def test_the_compaction_policy_carries_all_four_signals_to_the_model_layer():
    """Omitted, native compaction never engages and the thresholds fall back."""
    policy = ContextAssembler().compaction_policy()
    assert set(policy) == {
        "retain_recent_steps", "compact_after_steps",
        "compact_body_tokens", "fold_at_pressure",
    }
