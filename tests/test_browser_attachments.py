"""Whole screenshot selection; no browser, model, or network needed."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.agent.actor.game_builder_agent import GameBuilderAgent
from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
from agentevolver.agent.types import Agent


def browser(*shots, **kwargs):
    agent = BrowserAgent(**kwargs)
    agent._observed = {"extra": {"screenshots": [
        SimpleNamespace(screenshot=body, screenshot_path=path, screenshot_description=label)
        for body, path, label in shots
    ]}}
    return agent


def images(agent):
    return [part.image_url.url for message in agent.attachments() for part in message.content
            if part.type == "image_url"]


@pytest.mark.parametrize("agent_type", [Agent, GameBuilderAgent, WebsiteBuilderAgent])
def test_builders_share_current_environment_image_projection(agent_type):
    from agentevolver.agent.context.assembler import ContextAssembler
    from agentevolver.agent.context.conversation import Conversation

    agent = agent_type()
    agent._environment_observations = {"visual_environment": {"extra": {"screenshots": [
        SimpleNamespace(screenshot="before", screenshot_description="Before"),
        {"screenshot": "after", "screenshot_description": "Current"},
    ]}}}
    assert images(agent) == ["data:image/png;base64,after"]
    assert "visual_environment" in agent.attachments()[0].text
    conversation = Conversation(task="Inspect the current game")
    envelope = ContextAssembler().build_envelope(
        conversation, attachments=agent.attachments(),
    )
    assert [part.image_url.url for message in envelope.live for part in message.content
            if part.type == "image_url"] == ["data:image/png;base64,after"]
    assert conversation.items == []
    assert envelope.recent == () and envelope.checkpoint == ()
    agent._environment_observations = {"visual_environment": {"state": "Stopped", "extra": {"screenshots": []}}}
    assert images(agent) == []  # No old frame retained after stop/failure.


def test_shared_environment_screenshot_budget_and_deduplication():
    agent = Agent(max_screenshots=2)
    agent._environment_observations = {"visual": {"extra": {"screenshots": [
        {"screenshot": "a", "screenshot_description": "Old label"},
        {"screenshot": "a", "screenshot_description": "New label"},
        {"screenshot": "b", "screenshot_description": "Other frame"},
    ]}}}
    assert images(agent) == ["data:image/png;base64,a", "data:image/png;base64,b"]
    assert "Old label" not in agent.attachments()[0].text
    agent.max_screenshots = 0
    assert images(agent) == []


@pytest.mark.parametrize("protocol", ["responses", "responses_explicit", "chat", "anthropic"])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_changing_environment_frame_preserves_serialized_cache_prefix(protocol, checkpoint):
    import json

    from agentevolver.agent.context.assembler import ContextAssembler
    from agentevolver.message import AssistantMessage, CompactionMessage, SystemMessage
    from agentevolver.model.anthropic.serializer import AnthropicChatSerializer
    from agentevolver.model.llm_hub.response import serialize_input
    from agentevolver.model.llm_hub.serializer import LLMHubChatSerializer

    agent = GameBuilderAgent()
    agent.conversation.system = [SystemMessage(content="Stable rules")]
    agent.conversation.task = "Build the game"
    if checkpoint:
        state = {}
        if protocol.startswith("responses"):
            state = {"responses": {"compaction_items": [
                {"type": "compaction", "encrypted_content": "opaque-history"},
            ]}}
        elif protocol == "anthropic":
            state = {"anthropic": {"compaction_blocks": [
                {"type": "compaction", "content": "Earlier work"},
            ]}}
        agent.conversation.checkpoint = CompactionMessage(
            content="Earlier work", provider_state=state, compaction_scope="history",
        )
    agent.conversation.add_turn(AssistantMessage(content="Inspect the game"))
    requests = []
    for frame in ("frame-before", "frame-after"):
        agent._environment_observations = {"godot_environment": {"extra": {
            "screenshots": [{"screenshot": frame, "screenshot_description": "Game view"}],
        }}}
        messages = ContextAssembler().build(agent.conversation, attachments=agent.attachments())
        assert messages[-1].context_layer == "live" and not messages[-1].cache
        if protocol.startswith("responses"):
            wire = serialize_input(messages, cache=protocol == "responses_explicit")
        elif protocol == "chat":
            wire = LLMHubChatSerializer.serialize_messages(messages)
        else:
            wire = AnthropicChatSerializer.serialize_messages(messages)
        requests.append(json.dumps(wire, sort_keys=True))
    before, after = requests
    # Includes native checkpoint replay and actual serializer cache annotations.
    marker = "Current observation from godot_environment:"
    assert marker in before and marker in after
    assert before.split(marker)[0] == after.split(marker)[0]
    assert "frame-before" in before and "frame-before" not in after
    assert "frame-after" in after
    suffix = after.split(marker)[1]
    assert "cache_control" not in suffix and "prompt_cache_breakpoint" not in suffix
    assert "frame-" not in json.dumps([m.model_dump() for m in agent.conversation.items])


@pytest.mark.asyncio
async def test_base_observation_scope_and_failure_cannot_leak_previous_frame(monkeypatch):
    from agentevolver.environment.server import environment_manager

    state = {"state": "Live", "extra": {"screenshots": [{"screenshot": "frame"}]}}
    get_state = AsyncMock(side_effect=[state, OSError("disconnected")])
    monkeypatch.setattr(environment_manager, "get_state", get_state)
    agent = Agent(env_names=["visual"])
    ctx = SimpleNamespace(id="image-scope", extra={})
    await agent.environment_state(ctx)
    assert images(agent) == ["data:image/png;base64,frame"]
    assert "unavailable" in await agent.environment_state(ctx)
    assert images(agent) == []
    ctx.extra["environment_allowlist"] = []
    assert await agent.environment_state(ctx) == ""
    assert get_state.await_count == 2


@pytest.mark.parametrize("extra", [{"screenshots": [{"screenshot": []}]}, {"screenshots": "invalid"}, "invalid"])
def test_malformed_environment_image_payload_does_not_break_other_attachments(extra):
    agent = Agent()
    agent._environment_observations = {"broken": {"extra": extra}}
    assert agent.attachments() == []


def test_normal_step_only_attaches_current_image_but_failure_keeps_both():
    agent = browser(("before", "same.png", "Before"), ("after", "same.png", "Current"))
    assert images(agent) == ["data:image/png;base64,after"]
    agent._action_failed = True
    assert len(images(agent)) == 2


def test_pathless_images_are_not_deduplicated_by_missing_path():
    agent = browser(("a", None, "A"), ("b", None, "B"), screenshot_history="always")
    assert len(images(agent)) == 2


def test_identical_images_keep_the_latest_label_even_with_different_paths():
    agent = browser(("same", "old", "Old"), ("same", "new", "Current"), screenshot_history="always")
    assert len(images(agent)) == 1
    assert agent.attachments()[0].content[0].text == "\n[Current]"


def test_zero_screenshot_budget_really_disables_attachments():
    agent = browser(("current", "x", "Current"), max_screenshots=0)
    assert agent.attachments() == []


def test_a_folded_image_is_named_to_the_summariser_not_pasted_into_it():
    """`Message.text` stringifies an image part to its whole base64 data URL.

    Nothing puts an image in the conversation today — a screenshot is evidence for one
    step and is dropped after it — but the portable checkpoint renders whatever history
    holds, so one image reaching it would put megabytes of base64 into the summariser's
    prompt and again into the auditor's. The guard belongs with the renderer, not with an
    assumption about who fills the history.
    """
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.message.types import (
        ContentPartImage, ContentPartText, HumanMessage, ImageURL,
    )

    message = HumanMessage(content=[
        ContentPartText(text="the galaxy field renders, ~40 systems"),
        ContentPartImage(image_url=ImageURL(url="data:image/png;base64," + "x" * 4096)),
    ])
    rendered = Agent._render_for_checkpoint(message)
    assert "base64" not in rendered and "x" * 64 not in rendered
    assert "[image_url]" in rendered and "galaxy field renders" in rendered
