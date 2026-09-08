"""Whole screenshot selection; no browser, model, or network needed."""
from types import SimpleNamespace

from agentevolver.agent.actor.browser_agent import BrowserAgent


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
