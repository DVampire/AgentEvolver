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
from unittest.mock import patch

import pytest

from agentevolver.model.context import (
    _RETRY_JITTER,
    _RETRY_MAX_DELAY,
    _retry_delay,
    _record_retry,
)


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #
def test_the_wait_grows_with_each_attempt():
    """Instant retry is what made the old loop useless against a rate limit."""
    with patch("random.random", return_value=0.5):          # no jitter at the midpoint
        first, second, third = (_retry_delay(n) for n in (1, 2, 3))

    assert first < second < third


def test_the_wait_is_capped():
    """A provider that is down stays down. An uncapped exponential turns a failed call
    into a hung agent, which is a worse outcome than failing."""
    with patch("random.random", return_value=1.0):          # jitter at its maximum
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
    assert event.metadata["kind"] == "llm_retry"
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
        asyncio.run(_record_retry("session-1", **ATTEMPT))   # must not raise


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
        for match in re.finditer(r"^    max_retries: int = (\d+)", path.read_text(encoding="utf-8"), re.M):
            if match.group(1) != "0":
                offenders.append(f"{path.name}: {match.group(0).strip()}")

    assert not offenders, (
        "these providers give the vendor SDK its own retry budget, which multiplies with "
        f"the application loop: {offenders}"
    )
