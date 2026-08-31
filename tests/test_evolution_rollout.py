"""Candidate component versions progress through shadow, canary, and activation safely.

A regression must roll back automatically, while a healthy candidate must meet evidence
thresholds before it becomes active. The tests also pin deterministic cohort selection
and rollout state reporting.
"""

import pytest

from agentevolver.extension.rollout import (
    RolloutController,
    RolloutObservation,
    RolloutPhase,
    RolloutPolicy,
)


@pytest.mark.asyncio
async def test_shadow_regression_rolls_back_automatically():
    reverted = []

    async def rollback(reason):
        reverted.append(reason)

    controller = RolloutController()
    controller.begin(
        "tool:search",
        "1",
        "2",
        rollback,
        RolloutPolicy(min_shadow_samples=2),
    )
    for _ in range(2):
        rollout = await controller.record_shadow(
            "tool:search",
            RolloutObservation(success=True, score=1, latency_ms=10),
            RolloutObservation(success=False, score=0, latency_ms=20),
        )

    assert rollout.phase is RolloutPhase.REVERTED
    assert reverted and "shadow" in reverted[0]
    assert controller.select_version("tool:search", "session") == "1"


@pytest.mark.asyncio
async def test_clean_shadow_enters_canary_then_promotes_after_threshold():
    async def rollback(_reason):
        raise AssertionError("should not roll back")

    controller = RolloutController()
    policy = RolloutPolicy(
        min_shadow_samples=2,
        min_canary_samples=2,
        max_canary_samples=3,
        canary_fraction=0.25,
    )
    controller.begin("agent:worker", "4", "5", rollback, policy)
    for _ in range(2):
        rollout = await controller.record_shadow(
            "agent:worker",
            RolloutObservation(success=True, score=0.8, latency_ms=10),
            RolloutObservation(success=True, score=0.9, latency_ms=10),
        )
    assert rollout.phase is RolloutPhase.CANARY
    assert controller.select_version("agent:worker", "stable-key") in {"4", "5"}

    for _ in range(3):
        rollout = await controller.record_canary(
            "agent:worker",
            RolloutObservation(success=True, score=0.9, latency_ms=10),
        )
    assert rollout.phase is RolloutPhase.ACTIVE
    assert controller.select_version("agent:worker", "any") == "5"


@pytest.mark.asyncio
async def test_candidate_is_activated_only_after_canary_threshold():
    events = []

    async def rollback(reason):
        events.append(("rollback", reason))

    async def activate():
        events.append(("activate", "2"))

    controller = RolloutController()
    controller.begin(
        "tool:x",
        "1",
        "2",
        rollback,
        RolloutPolicy(
            min_shadow_samples=1,
            min_canary_samples=1,
            max_canary_samples=1,
        ),
        activate=activate,
    )
    assert events == []
    await controller.record_shadow(
        "tool:x",
        RolloutObservation(success=True),
        RolloutObservation(success=True),
    )
    rollout = await controller.record_canary(
        "tool:x",
        RolloutObservation(success=True),
    )

    assert rollout.phase is RolloutPhase.ACTIVE
    assert events == [("activate", "2")]


@pytest.mark.asyncio
async def test_activation_failure_reverts_to_baseline():
    events = []

    async def rollback(reason):
        events.append(("rollback", reason))

    async def activate():
        raise RuntimeError("registry unavailable")

    controller = RolloutController()
    controller.begin(
        "tool:x",
        "1",
        "2",
        rollback,
        RolloutPolicy(
            min_shadow_samples=1,
            min_canary_samples=1,
            max_canary_samples=1,
        ),
        activate=activate,
    )
    await controller.record_shadow(
        "tool:x",
        RolloutObservation(success=True),
        RolloutObservation(success=True),
    )
    rollout = await controller.record_canary(
        "tool:x",
        RolloutObservation(success=True),
    )

    assert rollout.phase is RolloutPhase.REVERTED
    assert events and "activation failed" in events[0][1]


@pytest.mark.asyncio
async def test_canary_failure_uses_baseline_threshold_and_reverts():
    reasons = []

    async def rollback(reason):
        reasons.append(reason)

    controller = RolloutController()
    controller.begin(
        "skill:x",
        "1",
        "2",
        rollback,
        RolloutPolicy(min_shadow_samples=1, min_canary_samples=2),
    )
    await controller.record_shadow(
        "skill:x",
        RolloutObservation(success=True),
        RolloutObservation(success=True),
    )
    await controller.record_canary("skill:x", RolloutObservation(success=False))
    rollout = await controller.record_canary("skill:x", RolloutObservation(success=False))
    assert rollout.phase is RolloutPhase.REVERTED
    assert reasons and "canary" in reasons[0]
