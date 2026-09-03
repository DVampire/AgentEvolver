"""A route that cannot accept images must say so, and nothing may pair one with a camera.

`BrowserEnvironment` attaches a screenshot to every observation. A text-only route does
not degrade on that — the relay answers 400 "Model do not support image input" and the
agent fails on EVERY call, three retries a step, contributing nothing to the round.

`llm_hub/deepseek-v4-flash` sat in the website demo's participant panel in exactly that
state. It survived because the llm_hub branch asserted `supports_vision=True` for the
whole relay rather than reading it per route, so the one fact that would have caught the
pairing said the opposite. The failure then read as a flaky stream ("failed before first
event") rather than as a model that cannot see.
"""

import pytest

from agentevolver.model.config import llm_hub_models


def _catalog():
    """Only names and flags are read, so the sizing arguments are placeholders."""
    return llm_hub_models(max_tokens=1, default_temperature=0.0, default_timeout=1.0)


def _entries():
    return [
        entry
        for group in _catalog().values()
        for entry in group
        if isinstance(entry, dict) and entry.get("model_name")
    ]


def test_the_catalog_has_routes_to_check():
    """Guards the guard: an empty catalog would pass everything below."""
    assert len(_entries()) >= 4


def test_a_text_only_route_is_declared_text_only():
    """Verified live against the relay: this one refuses an image part outright."""
    entry = next(e for e in _entries() if e["model_name"] == "llm_hub/deepseek-v4-flash")
    assert entry.get("supports_vision") is False


@pytest.mark.asyncio
async def test_the_declaration_survives_into_the_resolved_model_config():
    """The catalog flag has to reach `ModelConfig`, not stop at the dict.

    This is the half that was broken: the entry could say anything, because the branch
    that built the config passed a literal `True`.
    """
    from agentevolver.model import model_manager

    await model_manager.initialize()
    blind = model_manager.get_model_config("llm_hub/deepseek-v4-flash")
    seeing = model_manager.get_model_config("llm_hub/claude-opus-5")
    assert blind is not None and seeing is not None
    assert blind.supports_vision is False
    assert seeing.supports_vision is True


def test_no_browser_driving_role_is_routed_to_a_blind_model():
    """The website demo is the config that pairs models with a camera; check it.

    Read from the config rather than from a list kept here, so adding a fourth persona
    or changing a route is covered without editing this test.
    """
    from mmengine.config import Config

    config = Config.fromfile("configs/website_evolution_demo.py")
    blind = {
        entry["model_name"] for entry in _entries()
        if entry.get("supports_vision") is False
    }
    routes = [
        *[str(m) for m in config.website_user_models],
        str(config.browser_agent["model_name"]),
        str(config.website_user_agent["model_name"]),
    ]
    offenders = sorted({route for route in routes if route in blind})
    assert not offenders, (
        f"these browser-driving roles cannot see the screenshots they are sent: {offenders}"
    )
