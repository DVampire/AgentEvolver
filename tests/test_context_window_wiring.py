"""Where the context window comes from, and who is allowed to overrule it.

Two failures this file exists to prevent, both of which had already happened:

`ModelConfig` carried a `context_window` field, every catalog entry could declare one,
and not one of the nine registration sites passed it through — so every model in the
process ran on the 128k compatibility default while the spec said otherwise. A field
nothing writes is indistinguishable from a field nothing reads, and neither shows up in
a test that only checks the default.

And the default itself: the window is a *guess*, used to decide when to excerpt tool
results before sending. Guessing low invents a wall the provider does not have — the
request never leaves and history is folded to get under it. That is only safe to guess
high if the provider's own rejection is read back as the same recoverable condition,
which is what `provider_rejected_for_length` is for. The two halves have to stay
together, so they are tested together.
"""

from __future__ import annotations

from typing import List
from unittest.mock import patch

import pytest

from agentevolver.message import HumanMessage
from agentevolver.model import config as catalog
from agentevolver.model.context import ModelContextManager
from agentevolver.model.pressure import (
    DEFAULT_CONTEXT_WINDOW,
    ContextOverflowError,
    provider_rejected_for_length,
)
from agentevolver.model.types import ModelConfig, ModelContext


def _specs() -> List[dict]:
    """Every catalog entry, from every provider, flattened."""
    kwargs = dict(
        max_tokens=4096,
        default_temperature=0.7,
        default_timeout=600.0,
        default_plugins=None,
        default_reasoning={"reasoning_effort": "high"},
    )
    entries: List[dict] = []
    for name in dir(catalog):
        if not name.endswith("_models"):
            continue
        fn = getattr(catalog, name)
        if not callable(fn):
            continue
        import inspect

        accepted = set(inspect.signature(fn).parameters)
        result = fn(**{k: v for k, v in kwargs.items() if k in accepted})
        groups = result.values() if isinstance(result, dict) else [result]
        for group in groups:
            entries.extend(group)
    return entries


def test_a_declared_context_window_reaches_the_model_config():
    """The wiring, checked end to end rather than by reading the constructor.

    Asserted against the catalog rather than a fixture: a tenth registration site added
    without the keyword is the exact regression, and only a test that walks real specs
    would see it.
    """
    declared = {s["model_name"]: s["context_window"] for s in _specs() if s.get("context_window")}
    assert declared, "no catalog entry declares a context window; the test has nothing to check"

    manager = ModelContextManager()
    import asyncio

    async def _register():
        with patch.object(manager, "_create_client", side_effect=_noop_client):
            for init in (
                manager._initialize_openai_models,
                manager._initialize_openrouter_models,
                manager._initialize_llm_hub_models,
                manager._initialize_anthropic_models,
                manager._initialize_google_models,
            ):
                try:
                    await init()
                except Exception:  # a provider with no key registers nothing; fine
                    pass

    asyncio.run(_register())

    seen = {
        name: manager.models[name].context_window for name in declared if name in manager.models
    }
    assert seen, "no declared model registered; cannot tell wiring from absence"
    for name, window in seen.items():
        assert window == declared[name], (
            f"{name} declares {declared[name]} but registered as {window} — the "
            f"catalog value is not reaching ModelConfig"
        )


async def _noop_client(_cfg):
    return None


def test_the_two_models_actually_configured_declare_their_window():
    """The models the agent configs name are the ones a wrong default would hurt."""
    declared = {s["model_name"]: s.get("context_window") for s in _specs()}
    assert declared.get("llm_hub/claude-opus-5") == 1_000_000
    assert declared.get("llm_hub/gpt-5.6-sol") == 1_050_000


def test_only_verified_llm_hub_routes_declare_native_compaction():
    declared = {
        spec["model_name"]: bool(spec.get("native_compaction", False))
        for spec in _specs()
        if spec["model_name"].startswith("llm_hub/")
    }

    assert declared["llm_hub/claude-opus-5"] is True
    assert declared["llm_hub/gpt-5.6-sol"] is True
    assert declared["llm_hub/gpt-5.6-luna"] is False
    assert declared["llm_hub/deepseek-v4-flash"] is False
    assert declared["llm_hub/claude-fable-5-1"] is False


def test_fable_51_uses_verified_native_messages_route_and_high_effort():
    spec = next(s for s in _specs() if s["model_name"] == "llm_hub/claude-fable-5-1")
    assert spec["model_id"] == "claude-fable-5-1"
    assert spec["model_type"] == "anthropic/messages"
    assert spec["persisted_reasoning"] is True
    assert spec["context_window"] == 1_000_000
    assert spec["reasoning"] == {
        "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"},
    }
    assert "temperature" not in spec
    assert spec["fallback_model"] == "llm_hub/claude-opus-5"
    assert spec["cost"]["input"] == 10 / 1_000_000
    assert spec["cost"]["cache_read"] == 0.25 / 1_000_000


def test_the_default_is_not_below_what_the_configured_models_accept():
    """A default under the real window is a wall we invented.

    It is what produced `114,847 > 95,232` against a model that accepts a million.
    """
    assert DEFAULT_CONTEXT_WINDOW >= 1_000_000


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
        "This model's maximum context length is 128000 tokens, however you requested 200000",
        "prompt is too long: 1048577 tokens > 1048576 maximum",
        "The input token count exceeds the maximum number of tokens allowed",
        "Provider returned error: context length exceeded for this model",
        "Please reduce the length of the messages and try again",
    ],
)
def test_every_provider_way_of_saying_too_long_is_recognised(message):
    assert provider_rejected_for_length(RuntimeError(message)), message


@pytest.mark.parametrize(
    "message",
    [
        "max_tokens is too large: 200000 > 128000",  # output reservation, not history
        "rate limit exceeded",
        "connection reset by peer",
        "invalid api key",
        "no candidate channel serves this model",
    ],
)
def test_errors_that_folding_history_would_not_fix_are_left_alone(message):
    """The failure mode of a loose matcher.

    Treated as an overflow, each of these would fold the run's history — losing context
    permanently — and then fail anyway for the reason it actually failed. `max_tokens is
    too large` is the trap: it names two token counts and is about the output budget.
    """
    assert not provider_rejected_for_length(RuntimeError(message)), message


class _RejectsForLength:
    """A client whose provider states its own limit instead of ours."""

    def __init__(self):
        self.attempts = 0

    async def stream(self, **_kwargs):
        self.attempts += 1
        raise RuntimeError("Error code: 400 - This model's maximum context length is 200000 tokens")
        yield  # pragma: no cover  — makes this an async generator

    def set_api_key(self, _key):
        pass


@pytest.mark.asyncio
async def test_a_provider_length_rejection_becomes_a_recoverable_overflow():
    """The behaviour that makes guessing the window high safe.

    Untyped, this is an ordinary provider error: it is retried until the attempt budget
    is gone, every attempt sending the identical request, and reported as an outage. The
    agent's one recovery for an oversized request — fold history, rebuild, try again —
    keys off `ContextOverflowError`, so the type is what reaches it.
    """
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_type="chat/completions",
        model_id="p/main",
        provider="p",
        max_completion_tokens=500,
        context_window=1_000_000,
    )
    manager.model_clients["main"] = client = _RejectsForLength()

    async def _noop(**_kwargs):
        return None

    with pytest.raises(ContextOverflowError):
        with patch("agentevolver.model.context._record_request_snapshot", side_effect=_noop):
            async for _ in manager.stream(
                name="main",
                input={"messages": [HumanMessage(content="go")], "max_retries": 3},
                ctx=ModelContext(id="len-session"),
            ):
                pass

    assert client.attempts == 1, (
        "a request the provider called too long was sent again; the retries are spent "
        "on an outcome that was decided before the first call"
    )
