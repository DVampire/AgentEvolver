"""A release records who accepted it, per subscriber — not which turn they were on.

The gate asked `ref.turn_success[release_number]`, which assumes a subscriber's Nth turn
is always about release N. It is not: a subscriber that fails, is told what to fix, and
is asked to verify again produces turn 2 *about release 1*. The gate kept reading
`turn_success[1]`, so a first rejection was permanent and no later release could ever
ship.

Measured on a live run before this fix: the acceptance worker exhausted its 45-step
budget on release 1 without calling done_tool, and the builder then spent 58 of its 133
steps — 43% of the run — alternating deploy and done against a gate that could not open.
It ended cancelled.

Turn numbers are the runtime's immutable record of how many times a process ran. Which
release a turn was *about* belongs to this protocol, so this protocol records it.
"""

import time
from types import SimpleNamespace

import pytest

from agentevolver.tool.default.deployment.deploy import ACCEPTANCE_TIMEOUT_S, DeployTool


def _ctx(*, subscribers=("sub-a", "sub-b"), releases=1, contract_extra=None):
    contract = {
        "subscriber_job_ids": list(subscribers),
        "collected_turns": {job: 1 for job in subscribers},
        **(contract_extra or {}),
    }
    return SimpleNamespace(
        id="builder-session",
        extra={
            "website_runtime_contract": contract,
            "deployment_release_history": [
                {"release_number": n + 1} for n in range(releases)
            ],
        },
    )


def _accept(ctx, job_id, *, success, turn):
    return DeployTool.record_acceptance(ctx, job_id, success=success, turn=turn)


def test_a_release_with_every_verdict_in_lets_the_next_one_ship():
    ctx = _ctx()
    _accept(ctx, "sub-a", success=True, turn=1)
    _accept(ctx, "sub-b", success=True, turn=1)
    assert DeployTool._previous_release_blocker(ctx) == ""


def test_a_rejection_blocks_the_next_release_and_says_how_to_clear_it():
    """Blocking is correct. Blocking with no stated way out is the defect."""
    ctx = _ctx()
    _accept(ctx, "sub-a", success=True, turn=1)
    _accept(ctx, "sub-b", success=False, turn=1)
    blocker = DeployTool._previous_release_blocker(ctx)
    assert "rejected by sub-b" in blocker
    assert "send_message_tool" in blocker, "a blocker must name the move that clears it"


def test_a_passing_retry_replaces_an_earlier_rejection():
    """The exact case that deadlocked a run: turn 2 is about release 1, not release 2."""
    ctx = _ctx()
    _accept(ctx, "sub-a", success=True, turn=1)
    _accept(ctx, "sub-b", success=False, turn=1)
    assert DeployTool._previous_release_blocker(ctx) != ""

    state = _accept(ctx, "sub-b", success=True, turn=2)
    assert state == "accepted"
    assert DeployTool._previous_release_blocker(ctx) == ""


def test_a_retry_counts_attempts_without_losing_the_release_it_was_about():
    ctx = _ctx()
    for turn, ok in ((1, False), (2, False), (3, True)):
        _accept(ctx, "sub-a", success=ok, turn=turn)
    recorded = ctx.extra["website_runtime_contract"]["release_acceptance"]["1"]["sub-a"]
    assert recorded == {"status": "accepted", "attempts": 3, "turn": 3}


def test_a_subscriber_that_never_reports_is_waited_for_then_recorded_absent():
    """A dead subscriber is a quality fact, not a permanent hold on the pipeline."""
    ctx = _ctx()
    _accept(ctx, "sub-a", success=True, turn=1)

    blocker = DeployTool._previous_release_blocker(ctx)
    assert "not complete" in blocker and "sub-b" in blocker
    assert "waiting up to" in blocker

    contract = ctx.extra["website_runtime_contract"]
    contract["release_wait_started"]["1"] = time.time() - ACCEPTANCE_TIMEOUT_S - 1
    assert DeployTool._previous_release_blocker(ctx) == ""
    assert contract["release_acceptance"]["1"]["sub-b"]["status"] == "absent"


def test_a_second_release_starts_from_a_clean_sheet():
    """Acceptance is per release; release 2 must not inherit release 1's verdicts."""
    ctx = _ctx()
    _accept(ctx, "sub-a", success=True, turn=1)
    _accept(ctx, "sub-b", success=True, turn=1)
    assert DeployTool._previous_release_blocker(ctx) == ""

    ctx.extra["deployment_release_history"].append({"release_number": 2})
    blocker = DeployTool._previous_release_blocker(ctx)
    assert "sub-a" in blocker and "sub-b" in blocker, blocker


def test_no_release_yet_blocks_nothing():
    ctx = _ctx(releases=0)
    assert DeployTool._previous_release_blocker(ctx) == ""


@pytest.mark.asyncio
async def test_feedback_round_does_not_overwrite_artifact_version(monkeypatch):
    from agentevolver.deploy.types import SiteRecord
    from agentevolver.runtime import kernel

    async def publish(*args, **kwargs):
        return 3, "deployment.ready", SimpleNamespace(id="event-1")

    monkeypatch.setattr(kernel, "publish_scoped", publish)
    monkeypatch.setattr(DeployTool, "_access_urls", staticmethod(lambda rec: {}))
    record = SiteRecord(site_id="echo-ark", runtime="static", release_number=7)
    receipt = await DeployTool._publish_ready(record, action="deploy", ctx=_ctx(releases=0))
    assert receipt["release_number"] == 1
    assert receipt["version_number"] == record.release_number == 7
