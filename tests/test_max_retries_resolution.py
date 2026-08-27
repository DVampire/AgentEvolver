"""llm_hub routes claude-opus-5 through AWS Bedrock, which intermittently returns an empty
completion (finish_reason null, ~3 tokens). The default of 3 attempts leaves a longer bad
window as a failed step; a flaky route needs its own higher ceiling, and a per-call value
must still win. `_resolve_max_retries` settles that precedence in one place for both the
buffered and streaming call paths.
"""

from __future__ import annotations

import types

from agentevolver.model.context import _resolve_max_retries, _DEFAULT_MAX_RETRIES
from agentevolver.model.config import llm_hub_models


def _cfg(max_retries):
    return types.SimpleNamespace(max_retries=max_retries)


def test_a_per_call_value_wins_over_everything():
    assert _resolve_max_retries(2, _cfg(6)) == 2
    assert _resolve_max_retries(2, _cfg(None)) == 2
    # even zero is an explicit choice, not "unset"
    assert _resolve_max_retries(0, _cfg(6)) == 0


def test_the_model_config_ceiling_is_used_when_no_per_call_value():
    assert _resolve_max_retries(None, _cfg(6)) == 6


def test_it_falls_back_to_the_default_when_neither_is_set():
    assert _resolve_max_retries(None, _cfg(None)) == _DEFAULT_MAX_RETRIES
    assert _resolve_max_retries(None, None) == _DEFAULT_MAX_RETRIES


def test_a_configured_zero_is_honoured_not_treated_as_unset():
    # A route may want exactly one attempt (no retries); `0 or 3` would silently make it 3.
    assert _resolve_max_retries(None, _cfg(0)) == 0
    assert _resolve_max_retries(0, _cfg(6)) == 0


def test_a_config_without_the_attribute_still_resolves():
    # getattr guard: an object that predates the field must not raise.
    assert _resolve_max_retries(None, object()) == _DEFAULT_MAX_RETRIES


def test_a_route_may_omit_max_retries_and_take_the_default():
    # The catalog need not set max_retries at all — the opus route leaves it unset now that
    # a transient empty retries on a short flat wait, so isolated empties are cheap at the
    # default 3 and a sustained window is the relay's to fix, not more attempts here.
    specs = llm_hub_models(max_tokens=8000, default_temperature=None, default_timeout=600)
    opus = next(m for m in specs["chat"] if m["model_id"] == "claude-opus-5")
    assert opus.get("max_retries") is None
    assert _resolve_max_retries(None, _cfg(opus.get("max_retries"))) == _DEFAULT_MAX_RETRIES
