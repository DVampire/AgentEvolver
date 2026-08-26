"""llm_hub's Bedrock-backed opus route intermittently returns an empty completion. The
retry loop backs off before each retry — but an empty completion is not a rate limit: the
next call almost always answers it, so the exponential climb (up to the 30s cap) meant for
rate limits and half-open connections just burns wall clock. On one run, 84 empties spent
6.3 minutes in backoff that bought nothing. Empties get a short flat wait; everything else
keeps the long climb, where retrying sooner would only re-trigger the same rejection.
"""

from __future__ import annotations

from agentevolver.model.context import (
    _is_transient_empty,
    _retry_delay,
    _EMPTY_COMPLETION_RETRY_DELAY,
    _RETRY_MAX_DELAY,
)


def test_an_empty_completion_is_recognised():
    assert _is_transient_empty(Exception("Model returned empty message"))
    assert _is_transient_empty(Exception("Fallback returned empty message"))
    # case-insensitive, and matches when wrapped in a longer string
    assert _is_transient_empty(Exception("llm_hub/claude-opus-5: Model returned EMPTY MESSAGE"))


def test_real_failures_are_not_treated_as_empty():
    assert not _is_transient_empty(Exception("429 Too Many Requests"))
    assert not _is_transient_empty(Exception("peer closed connection without sending a complete message body"))
    assert not _is_transient_empty(Exception("Read timed out"))
    assert not _is_transient_empty(Exception("Model returned success=False"))


def test_the_empty_wait_is_short_and_flat_well_under_the_backoff_climb():
    # The whole point: an empty's wait must be far below what the exponential backoff would
    # impose on later attempts, so a window of empties does not cost minutes.
    assert _EMPTY_COMPLETION_RETRY_DELAY <= 1.0
    # By the 3rd+ attempt the exponential backoff is already several seconds and climbs to
    # the 30s cap; the flat empty wait must stay well below that.
    assert _EMPTY_COMPLETION_RETRY_DELAY < _retry_delay(3)
    assert _EMPTY_COMPLETION_RETRY_DELAY < _RETRY_MAX_DELAY


def test_backoff_climbs_for_non_empty_failures():
    # Rate limits / dropped connections still get the escalating wait — retrying instantly
    # there only re-hits the same failure.
    assert _retry_delay(5) > _retry_delay(1)
