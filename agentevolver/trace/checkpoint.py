"""Semantic durability policy for model requests, steps, and external effects."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from agentevolver.logger import logger


INTEGRITY_POLICY_VERSION = 1
TRACE_INTEGRITY_PROFILE_KEY = "trace_integrity_profile"


class TraceIntegrityProfile(str, Enum):
    """How a run reacts when Trace cannot prove a semantic boundary is durable."""

    INTERACTIVE = "interactive"
    TRAINING = "training"
    HIGH_RISK = "high_risk"

    @property
    def required(self) -> bool:
        return self in (self.TRAINING, self.HIGH_RISK)


class TraceCheckpointBoundary(str, Enum):
    MODEL_REQUEST = "before_model_request"
    EXTERNAL_EFFECT = "before_external_effect"
    STEP_END = "step_end"


class TraceIntegrityError(RuntimeError):
    """A required Trace boundary could not be made durable."""


def resolve_integrity_profile(
    value: Any = None, *, ctx: Any = None,
) -> TraceIntegrityProfile:
    """Resolve explicit → context → global configuration, refusing unknown values."""
    selected = value
    if selected is None and ctx is not None:
        selected = (getattr(ctx, "extra", {}) or {}).get(TRACE_INTEGRITY_PROFILE_KEY)
    if selected is None:
        try:
            from agentevolver.config import config
            selected = getattr(config, TRACE_INTEGRITY_PROFILE_KEY, None)
        except Exception:  # noqa: BLE001 - config may not be initialized in isolated tests
            selected = None
    selected = selected or TraceIntegrityProfile.INTERACTIVE.value
    aliases = {
        "best_effort": TraceIntegrityProfile.INTERACTIVE.value,
        "strict": TraceIntegrityProfile.TRAINING.value,
        "high-risk": TraceIntegrityProfile.HIGH_RISK.value,
    }
    normalized = aliases.get(str(selected).strip().lower(), str(selected).strip().lower())
    try:
        return TraceIntegrityProfile(normalized)
    except ValueError as exc:
        choices = ", ".join(profile.value for profile in TraceIntegrityProfile)
        raise ValueError(
            f"unknown trace integrity profile {selected!r}; use {choices}"
        ) from exc


async def _integrity_failure(
    *,
    session_id: str,
    boundary: TraceCheckpointBoundary,
    profile: TraceIntegrityProfile,
    issue: str,
    metadata: Optional[Dict[str, Any]] = None,
    cause: Optional[BaseException] = None,
) -> bool:
    """Record one deduplicated degradation fact, then enforce the selected profile."""
    from agentevolver.trace.server import trace_manager
    from agentevolver.trace.types import TraceEvent, TraceEventType

    if trace_manager.should_report_integrity_gap(session_id, boundary.value, issue):
        event_metadata = {
            "kind": "integrity_degraded",
            "policy_version": INTEGRITY_POLICY_VERSION,
            "profile": profile.value,
            "boundary": boundary.value,
            **dict(metadata or {}),
        }
        try:
            await trace_manager.emit(TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=session_id,
                label=f"Trace integrity degraded at {boundary.value}",
                success=False,
                error=issue,
                metadata=event_metadata,
                # Dropping this changes whether a training consumer may trust the run.
                ignorable=False,
            ))
        except Exception as emit_error:  # noqa: BLE001 - preserve the original failure
            logger.error(f"| ❌ Could not emit integrity degradation fact: {emit_error}")
    message = (
        f"Trace integrity checkpoint {boundary.value!r} failed for session "
        f"{session_id!r} under profile {profile.value!r}: {issue}"
    )
    if profile.required:
        raise TraceIntegrityError(message) from cause
    logger.error(f"| ⚠️ {message}; continuing in interactive mode")
    return False


async def checkpoint_trace(
    session_id: str,
    boundary: TraceCheckpointBoundary,
    *,
    profile: Any = None,
    ctx: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> bool:
    """Make all preceding Trace facts durable at one semantic boundary.

    Interactive runs retain existing availability semantics. Training and high-risk runs
    require an active writer, a drained queue, no dropped events, and no writer failure.
    A Session remains invalid after a permanent gap; a later successful flush cannot
    recreate the missing event.
    """
    from agentevolver.trace.server import trace_manager

    selected = resolve_integrity_profile(profile, ctx=ctx)
    session_id = str(session_id or "")
    if selected.required and not session_id:
        raise TraceIntegrityError(
            f"Trace integrity checkpoint {boundary.value!r} requires a real session id "
            f"under profile {selected.value!r}"
        )
    if not trace_manager.running:
        if selected.required:
            return await _integrity_failure(
                session_id=session_id,
                boundary=boundary,
                profile=selected,
                issue="TraceManager is not running",
                metadata=metadata,
            )
        # Preserve the public flush seam for observational callers and tests. The real
        # manager returns immediately while stopped; an integration may still wrap it.
        try:
            return await trace_manager.flush(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return await _integrity_failure(
                session_id=session_id,
                boundary=boundary,
                profile=selected,
                issue=f"flush raised {type(exc).__name__}: {exc}",
                metadata=metadata,
                cause=exc,
            )
    try:
        flushed = await trace_manager.flush(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - policy decides whether the run proceeds
        return await _integrity_failure(
            session_id=session_id,
            boundary=boundary,
            profile=selected,
            issue=f"flush raised {type(exc).__name__}: {exc}",
            metadata=metadata,
            cause=exc,
        )
    issue = trace_manager.integrity_issue(session_id)
    if flushed and issue is None:
        return True
    return await _integrity_failure(
        session_id=session_id,
        boundary=boundary,
        profile=selected,
        issue=issue or "Trace flush timed out before the queue drained",
        metadata=metadata,
    )


async def report_trace_integrity_failure(
    session_id: str,
    boundary: TraceCheckpointBoundary,
    error: BaseException,
    *,
    profile: Any = None,
    ctx: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Apply the same policy when constructing/emitting the boundary fact itself fails."""
    selected = resolve_integrity_profile(profile, ctx=ctx)
    return await _integrity_failure(
        session_id=session_id,
        boundary=boundary,
        profile=selected,
        issue=f"{type(error).__name__}: {error}",
        metadata=metadata,
        cause=error,
    )


__all__ = [
    "INTEGRITY_POLICY_VERSION",
    "TRACE_INTEGRITY_PROFILE_KEY",
    "TraceIntegrityProfile",
    "TraceCheckpointBoundary",
    "TraceIntegrityError",
    "resolve_integrity_profile",
    "checkpoint_trace",
    "report_trace_integrity_failure",
]
