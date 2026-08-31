"""Measured baseline → shadow → canary rollout with automatic rollback."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Dict, Optional

from pydantic import BaseModel, Field


class RolloutPhase(str, Enum):
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    REVERTED = "reverted"


class RolloutPolicy(BaseModel):
    min_shadow_samples: int = Field(default=3, ge=1)
    min_canary_samples: int = Field(default=10, ge=1)
    max_canary_samples: int = Field(default=50, ge=1)
    canary_fraction: float = Field(default=0.1, gt=0, le=1)
    max_failure_rate_delta: float = Field(default=0.05, ge=0)
    min_score_delta: float = -0.02
    max_latency_ratio: float = Field(default=1.25, ge=1)


class RolloutObservation(BaseModel):
    success: bool
    score: Optional[float] = None
    latency_ms: Optional[float] = Field(default=None, ge=0)


@dataclass
class _Metrics:
    samples: int = 0
    failures: int = 0
    score_total: float = 0.0
    score_samples: int = 0
    latency_total: float = 0.0
    latency_samples: int = 0

    def add(self, value: RolloutObservation) -> None:
        self.samples += 1
        self.failures += int(not value.success)
        if value.score is not None:
            self.score_total += value.score
            self.score_samples += 1
        if value.latency_ms is not None:
            self.latency_total += value.latency_ms
            self.latency_samples += 1

    def snapshot(self) -> Dict[str, Optional[float] | int]:
        return {
            "samples": self.samples,
            "failure_rate": self.failures / self.samples if self.samples else 0.0,
            "mean_score": (
                self.score_total / self.score_samples if self.score_samples else None
            ),
            "mean_latency_ms": (
                self.latency_total / self.latency_samples
                if self.latency_samples else None
            ),
        }


Rollback = Callable[[str], Awaitable[None]]
Activate = Callable[[], Awaitable[None]]


@dataclass
class Rollout:
    key: str
    baseline_version: str
    candidate_version: str
    rollback: Rollback
    activate: Optional[Activate]
    policy: RolloutPolicy
    phase: RolloutPhase = RolloutPhase.SHADOW
    reason: str = ""
    baseline: _Metrics = field(default_factory=_Metrics)
    candidate: _Metrics = field(default_factory=_Metrics)
    canary: _Metrics = field(default_factory=_Metrics)

    def status(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "phase": self.phase.value,
            "baseline_version": self.baseline_version,
            "candidate_version": self.candidate_version,
            "reason": self.reason,
            "baseline": self.baseline.snapshot(),
            "candidate_shadow": self.candidate.snapshot(),
            "candidate_canary": self.canary.snapshot(),
            "policy": self.policy.model_dump(),
        }


class RolloutController:
    """Control plane for evidence-based extension activation."""

    def __init__(self) -> None:
        self._rollouts: Dict[str, Rollout] = {}
        self._lock = asyncio.Lock()

    def begin(
        self,
        key: str,
        baseline_version: str,
        candidate_version: str,
        rollback: Rollback,
        policy: Optional[RolloutPolicy] = None,
        activate: Optional[Activate] = None,
    ) -> Rollout:
        rollout = Rollout(
            key=key,
            baseline_version=baseline_version,
            candidate_version=candidate_version,
            rollback=rollback,
            activate=activate,
            policy=policy or RolloutPolicy(),
        )
        self._rollouts[key] = rollout
        return rollout

    def get(self, key: str) -> Optional[Rollout]:
        return self._rollouts.get(key)

    def select_version(self, key: str, traffic_key: str) -> Optional[str]:
        rollout = self._rollouts.get(key)
        if rollout is None:
            return None
        if rollout.phase is RolloutPhase.SHADOW:
            return rollout.baseline_version
        if rollout.phase is RolloutPhase.CANARY:
            digest = hashlib.sha256(f"{key}:{traffic_key}".encode()).digest()
            fraction = int.from_bytes(digest[:8], "big") / float(2**64)
            return (
                rollout.candidate_version
                if fraction < rollout.policy.canary_fraction
                else rollout.baseline_version
            )
        if rollout.phase is RolloutPhase.ACTIVE:
            return rollout.candidate_version
        return rollout.baseline_version

    @staticmethod
    def _regression(rollout: Rollout, candidate: _Metrics) -> str:
        baseline = rollout.baseline.snapshot()
        measured = candidate.snapshot()
        failure_delta = float(measured["failure_rate"]) - float(baseline["failure_rate"])
        if failure_delta > rollout.policy.max_failure_rate_delta:
            return f"failure rate regressed by {failure_delta:.3f}"
        base_score, candidate_score = baseline["mean_score"], measured["mean_score"]
        if base_score is not None and candidate_score is not None:
            delta = float(candidate_score) - float(base_score)
            if delta < rollout.policy.min_score_delta:
                return f"mean score regressed by {delta:.3f}"
        base_latency, candidate_latency = baseline["mean_latency_ms"], measured["mean_latency_ms"]
        if base_latency and candidate_latency:
            ratio = float(candidate_latency) / float(base_latency)
            if ratio > rollout.policy.max_latency_ratio:
                return f"mean latency ratio {ratio:.3f} exceeded threshold"
        return ""

    async def _revert(self, rollout: Rollout, reason: str) -> Rollout:
        if rollout.phase is RolloutPhase.REVERTED:
            return rollout
        await rollout.rollback(reason)
        rollout.phase = RolloutPhase.REVERTED
        rollout.reason = reason
        return rollout

    async def record_shadow(
        self,
        key: str,
        baseline: RolloutObservation,
        candidate: RolloutObservation,
    ) -> Rollout:
        async with self._lock:
            rollout = self._rollouts[key]
            if rollout.phase is not RolloutPhase.SHADOW:
                return rollout
            rollout.baseline.add(baseline)
            rollout.candidate.add(candidate)
            if rollout.candidate.samples < rollout.policy.min_shadow_samples:
                return rollout
            regression = self._regression(rollout, rollout.candidate)
            if regression:
                return await self._revert(rollout, f"shadow: {regression}")
            rollout.phase = RolloutPhase.CANARY
            return rollout

    async def record_canary(
        self, key: str, candidate: RolloutObservation,
    ) -> Rollout:
        async with self._lock:
            rollout = self._rollouts[key]
            if rollout.phase is not RolloutPhase.CANARY:
                return rollout
            rollout.canary.add(candidate)
            if rollout.canary.samples >= rollout.policy.min_canary_samples:
                regression = self._regression(rollout, rollout.canary)
                if regression:
                    return await self._revert(rollout, f"canary: {regression}")
            if rollout.canary.samples >= rollout.policy.max_canary_samples:
                try:
                    if rollout.activate is not None:
                        await rollout.activate()
                except Exception as error:
                    return await self._revert(
                        rollout, f"candidate activation failed: {error}",
                    )
                rollout.phase = RolloutPhase.ACTIVE
            return rollout


__all__ = [
    "Rollout",
    "RolloutController",
    "RolloutObservation",
    "RolloutPhase",
    "RolloutPolicy",
]
