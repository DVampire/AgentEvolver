"""A retried model call leaves a record, waits between attempts, and retries in one place.

This repository's product is trajectories. A call that failed twice and succeeded on the
third try used to be indistinguishable from one that succeeded immediately: the retry loop
reported only its last attempt, so the trajectory said `success` and the two earlier
failures existed nowhere except a log line nobody joins back to the run. A training sample
labelled "the model got this right" when the model failed twice first is not the sample it
claims to be.

Two other defects lived in the same loop. It retried **instantly**, which re-sends into the
same rate limit — three attempts failing three times for one reason. And every provider
also handed its own `max_retries` to the vendor SDK, so the two budgets multiplied: three
application attempts over five SDK attempts is fifteen requests, a number nobody chose and
nobody could see.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentevolver.model.config import llm_hub_models
from agentevolver.model.context import (
    _DEFAULT_MAX_RETRIES,
    _EMPTY_COMPLETION_RETRY_DELAY,
    _RETRY_JITTER,
    _RETRY_MAX_DELAY,
    _is_transient_empty,
    _record_retry,
    _resolve_max_retries,
    _retry_delay,
)


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #
def test_the_wait_grows_with_each_attempt():
    """Instant retry is what made the old loop useless against a rate limit."""
    with patch("random.random", return_value=0.5):  # no jitter at the midpoint
        first, second, third = (_retry_delay(n) for n in (1, 2, 3))

    assert first < second < third


def test_the_wait_is_capped():
    """A provider that is down stays down. An uncapped exponential turns a failed call
    into a hung agent, which is a worse outcome than failing."""
    with patch("random.random", return_value=1.0):  # jitter at its maximum
        assert _retry_delay(50) <= _RETRY_MAX_DELAY


def test_the_wait_is_jittered_in_both_directions():
    """Without jitter every agent that hit one rate limit retries in lockstep and
    re-creates the burst that caused it.

    Checked in both directions because one-sided jitter — only ever adding — still leaves
    the whole fleet clustered, just later.
    """
    with patch("random.random", return_value=0.0):
        low = _retry_delay(1)
    with patch("random.random", return_value=1.0):
        high = _retry_delay(1)
    with patch("random.random", return_value=0.5):
        middle = _retry_delay(1)

    assert low < middle < high
    assert low == pytest.approx(middle * (1 - _RETRY_JITTER))


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #
def _emitted(session_id, **kwargs):
    """Run `_record_retry` against a stubbed trace and return the events it emitted."""
    events = []

    async def capture(event):
        events.append(event)

    with patch("agentevolver.trace.server.trace_manager.emit", side_effect=capture):
        asyncio.run(_record_retry(session_id, **kwargs))
    return events


ATTEMPT = dict(model="gpt-test", attempt=1, total=3, error="boom", delay=2.0, caller="agent")


def test_a_failed_attempt_is_written_to_the_trace():
    """The record that makes a retried success distinguishable from a clean one."""
    (event,) = _emitted("session-1", **ATTEMPT)

    assert event.session_id == "session-1"
    assert event.success is False
    assert event.metadata["type"] == "llm_retry"
    assert (event.metadata["attempt"], event.metadata["max_attempts"]) == (1, 3)


def test_the_record_names_the_model_the_error_and_the_caller():
    """Each of these answers a question the reader has when they find the event.

    Without the model, a mixed-provider run cannot be diagnosed; without the caller, a
    retry cannot be attributed to the step that caused it; without the error, the record
    says only that something went wrong.
    """
    (event,) = _emitted("session-1", **ATTEMPT)

    assert event.metadata["model"] == "gpt-test"
    assert event.metadata["caller"] == "agent"
    assert event.error == "boom"


def test_the_record_says_how_long_the_wait_will_be():
    """Recorded before the sleep, not after.

    A trace that only says "there was a retry" cannot explain a slow run; one that says
    the wait was 8 seconds can. It also distinguishes a retry that is about to happen from
    the final attempt, which has no wait at all.
    """
    (scheduled,) = _emitted("session-1", **{**ATTEMPT, "delay": 8.0})
    (final,) = _emitted("session-1", **{**ATTEMPT, "attempt": 3, "delay": None})

    assert scheduled.metadata["delay_seconds"] == 8.0
    assert final.metadata["delay_seconds"] is None


def test_a_call_with_no_session_records_nothing_rather_than_failing():
    """Model calls happen outside a session — a health check, a script.

    Those have nowhere to write, and refusing to run them would make the trace a
    requirement for calling a model.
    """
    assert _emitted(None, **ATTEMPT) == []
    assert _emitted("", **ATTEMPT) == []


def test_a_trace_that_cannot_be_written_does_not_break_the_call():
    """The recording is worth strictly less than the request in flight.

    This is the inversion that makes it safe to record on a hot path: if the trace layer
    is unavailable, the model call still proceeds. The failure is logged at debug and
    goes no further.
    """

    async def exploding(_event):
        raise RuntimeError("trace is down")

    with patch("agentevolver.trace.server.trace_manager.emit", side_effect=exploding):
        asyncio.run(_record_retry("session-1", **ATTEMPT))  # must not raise


# --------------------------------------------------------------------------- #
# One retry layer, not two
# --------------------------------------------------------------------------- #
def test_no_provider_hands_the_vendor_sdk_a_retry_budget_of_its_own():
    """Two retry layers multiply, and the inner one is invisible.

    Three application attempts over a provider default of five is fifteen requests for one
    logical call — a number nobody chose, whose failures reach neither the log nor the
    trajectory. Retries belong in `ModelContextManager.__call__`, which backs off, records
    each attempt, and knows the caller.

    Discovered from the source rather than listed, so a provider added later is covered by
    this test existing.
    """
    import re
    from pathlib import Path

    model_root = Path(__file__).resolve().parents[1] / "agentevolver" / "model"
    offenders = []
    for path in sorted(model_root.rglob("*.py")):
        for match in re.finditer(
            r"^    max_retries: int = (\d+)", path.read_text(encoding="utf-8"), re.M
        ):
            if match.group(1) != "0":
                offenders.append(f"{path.name}: {match.group(0).strip()}")

    assert not offenders, (
        "these providers give the vendor SDK its own retry budget, which multiplies with "
        f"the application loop: {offenders}"
    )


# --------------------------------------------------------------------------- #
# Transient empty completions
# --------------------------------------------------------------------------- #
def test_an_empty_completion_is_recognised():
    """Empty relay responses get the short retry path, regardless of casing or prefix."""
    assert _is_transient_empty(Exception("Model returned empty message"))
    assert _is_transient_empty(Exception("Fallback returned empty message"))
    assert _is_transient_empty(Exception("llm_hub/claude-opus-5: Model returned EMPTY MESSAGE"))


def test_real_failures_are_not_treated_as_empty():
    """Rate limits and transport errors still require the ordinary exponential backoff."""
    errors = (
        "429 Too Many Requests",
        "peer closed connection without sending a complete message body",
        "Read timed out",
        "Model returned success=False",
    )
    assert all(not _is_transient_empty(Exception(message)) for message in errors)


def test_the_empty_wait_is_short_and_flat_well_under_the_backoff_climb():
    """A window of empty relay responses must not turn into minutes of sleeping."""
    assert _EMPTY_COMPLETION_RETRY_DELAY <= 1.0
    assert _EMPTY_COMPLETION_RETRY_DELAY < _retry_delay(3)
    assert _EMPTY_COMPLETION_RETRY_DELAY < _RETRY_MAX_DELAY


# --------------------------------------------------------------------------- #
# Retry-budget precedence
# --------------------------------------------------------------------------- #
def _model_config(max_retries):
    """Minimal provider config accepted by `_resolve_max_retries`."""
    return SimpleNamespace(max_retries=max_retries)


def test_a_per_call_retry_budget_wins_over_everything():
    assert _resolve_max_retries(2, _model_config(6)) == 2
    assert _resolve_max_retries(2, _model_config(None)) == 2
    assert _resolve_max_retries(0, _model_config(6)) == 0


def test_the_model_config_retry_budget_is_used_when_no_per_call_value():
    assert _resolve_max_retries(None, _model_config(6)) == 6


def test_the_retry_budget_falls_back_to_the_default_when_neither_is_set():
    assert _resolve_max_retries(None, _model_config(None)) == _DEFAULT_MAX_RETRIES
    assert _resolve_max_retries(None, None) == _DEFAULT_MAX_RETRIES


def test_a_configured_zero_retry_budget_is_honoured():
    """Zero means one attempt, not an absent value that should receive the default."""
    assert _resolve_max_retries(None, _model_config(0)) == 0
    assert _resolve_max_retries(0, _model_config(6)) == 0


def test_a_legacy_config_without_the_retry_attribute_still_resolves():
    assert _resolve_max_retries(None, object()) == _DEFAULT_MAX_RETRIES


def test_a_route_may_omit_max_retries_and_take_the_default():
    """The Opus relay relies on the shared default and short empty-response delay."""
    specs = llm_hub_models(
        max_tokens=8000,
        default_temperature=None,
        default_timeout=600,
    )
    opus = next(model for model in specs["chat"] if model["model_id"] == "claude-opus-5")

    assert opus.get("max_retries") is None
    assert (
        _resolve_max_retries(None, _model_config(opus.get("max_retries"))) == _DEFAULT_MAX_RETRIES
    )
