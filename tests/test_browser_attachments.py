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
