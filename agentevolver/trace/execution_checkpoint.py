"""Derive a conservative crash-resume checkpoint from durable Trace events."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.trace.types import TraceEvent, TraceEventType

EXECUTION_CHECKPOINT_VERSION = 1


class UnsettledCall(BaseModel):
    """A start record with no matching result.

    Trace proves the intent was durable before a possible effect, but a process death
    cannot prove on which side of the effect it happened.  Such a call is never retried
    automatically; a host must reconcile it or obtain confirmation.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    action_type: str
    action_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    step_number: Optional[int] = None
    action_index: Optional[int] = None
    requires_confirmation: bool = True


class EffectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    action_name: str
    success: bool
    execution: Dict[str, Any] = Field(default_factory=dict)


class ExecutionCheckpoint(BaseModel):
    """Everything a resumed host needs before it may advance the agent loop."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = EXECUTION_CHECKPOINT_VERSION
    session_id: str
    source_last_seq: int = -1
    state: Literal["empty", "resumable", "needs_confirmation", "completed"] = "empty"
    task_id: Optional[str] = None
    agent_name: Optional[str] = None
    next_step: int = 1
    latest_request_snapshot_id: Optional[str] = None
    latest_model_route: Optional[str] = None
    provider_state: Dict[str, Any] = Field(default_factory=dict)
    unsettled_calls: List[UnsettledCall] = Field(default_factory=list)
    effect_receipts: List[EffectReceipt] = Field(default_factory=list)
    interrupted_compactions: List[str] = Field(default_factory=list)
    workspace_fingerprint: Optional[str] = None
    completion_success: Optional[bool] = None
    completion_result: Optional[str] = None

    @property
    def may_resume_automatically(self) -> bool:
        return self.state == "resumable"


def _call_key(event: TraceEvent) -> str:
    metadata = event.metadata or {}
    call_id = str(metadata.get("call_id") or "")
    if call_id:
        return call_id
    return ":".join((
        event.action_type or "action",
        str(event.step_number if event.step_number is not None else ""),
        str(event.action_index if event.action_index is not None else ""),
        event.action_name or "",
    ))


def derive_execution_checkpoint(
    session_id: str,
    events: Iterable[TraceEvent],
    *,
    workspace_fingerprint: Optional[str] = None,
) -> ExecutionCheckpoint:
    """Project one version-checked event stream into a fail-closed resume decision."""
    ordered = sorted(
        (event for event in events if event.session_id == session_id),
        key=lambda event: int(event.seq_no if event.seq_no is not None else -1),
    )
    checkpoint = ExecutionCheckpoint(
        session_id=session_id,
        workspace_fingerprint=workspace_fingerprint,
    )
    if not ordered:
        return checkpoint

    checkpoint.source_last_seq = max(
        int(event.seq_no) for event in ordered if event.seq_no is not None
    ) if any(event.seq_no is not None for event in ordered) else -1
    starts: Dict[str, TraceEvent] = {}
    background_starts: Dict[str, TraceEvent] = {}
    transactions: Dict[str, set[str]] = {}
    completed = False

    for event in ordered:
        checkpoint.task_id = event.task_id or checkpoint.task_id
        checkpoint.agent_name = event.agent_name or checkpoint.agent_name
        if event.step_number is not None:
            checkpoint.next_step = max(checkpoint.next_step, int(event.step_number) + 1)
        if event.event_type is TraceEventType.MODEL_REQUEST:
            snapshot = event.input or {}
            checkpoint.latest_request_snapshot_id = str(
                (event.metadata or {}).get("request_snapshot_id")
                or snapshot.get("snapshot_id") or ""
            ) or None
            checkpoint.latest_model_route = str(snapshot.get("routed_model") or "") or None
            parameters = snapshot.get("parameters") or {}
            operation = str(parameters.get("operation") or "")
            if parameters.get("background") or operation == "background.cancel":
                key = str(
                    (event.metadata or {}).get("request_snapshot_id")
                    or snapshot.get("snapshot_id") or event.id
                )
                background_starts[key] = event
        elif event.event_type is TraceEventType.AGENT_CALL:
            checkpoint.provider_state = dict(event.provider_state or {})
        elif event.event_type in (TraceEventType.TOOL_START, TraceEventType.SKILL_START):
            starts[_call_key(event)] = event
        elif event.event_type in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            key = _call_key(event)
            starts.pop(key, None)
            checkpoint.effect_receipts.append(EffectReceipt(
                call_id=key,
                action_name=event.action_name or "",
                success=bool(event.success),
                execution=dict((event.metadata or {}).get("execution") or {}),
            ))
        elif event.event_type is TraceEventType.AGENT_END:
            completed = True
            checkpoint.completion_success = bool(event.success)
            checkpoint.completion_result = (
                str(event.output) if event.output is not None else event.message
            )
        elif (
            event.event_type is TraceEventType.CUSTOM
            and (event.metadata or {}).get("type") == "compaction_transaction"
        ):
            transaction_id = str((event.metadata or {}).get("transaction_id") or "")
            if transaction_id:
                transactions.setdefault(transaction_id, set()).add(
                    str((event.metadata or {}).get("phase") or "")
                )
        elif (
            event.event_type is TraceEventType.CUSTOM
            and (event.metadata or {}).get("type") == "responses_background_effect"
            and (event.metadata or {}).get("phase") == "result"
        ):
            key = str((event.metadata or {}).get("request_snapshot_id") or "")
            if key:
                background_starts.pop(key, None)
            else:
                operation = str((event.metadata or {}).get("operation") or "")
                for pending_key, pending in list(background_starts.items()):
                    parameters = (pending.input or {}).get("parameters") or {}
                    if str(parameters.get("operation") or "").endswith(operation):
                        background_starts.pop(pending_key, None)
                        break

    checkpoint.unsettled_calls = [
        UnsettledCall(
            call_id=key,
            action_type=event.action_type or "action",
            action_name=event.action_name or "",
            arguments=dict(event.input or {}),
            step_number=event.step_number,
            action_index=event.action_index,
        )
        for key, event in sorted(starts.items())
    ]
    checkpoint.unsettled_calls.extend(
        UnsettledCall(
            call_id=key,
            action_type="model_effect",
            action_name=(
                str(((event.input or {}).get("parameters") or {}).get("operation") or "background.create")
            ),
            arguments=dict((event.input or {}).get("parameters") or {}),
            step_number=event.step_number,
        )
        for key, event in sorted(background_starts.items())
    )
    checkpoint.interrupted_compactions = sorted(
        transaction_id for transaction_id, phases in transactions.items()
        if "started" in phases and not ({"committed", "aborted"} & phases)
    )
    # An unresolved effect always wins over a nominal AGENT_END. A corrupted, imported
    # or partially flushed log must never be declared safe merely because a later end
    # marker exists; the provider/tool effect still requires reconciliation.
    if checkpoint.unsettled_calls:
        checkpoint.state = "needs_confirmation"
    elif completed:
        checkpoint.state = "completed"
    else:
        checkpoint.state = "resumable"
    return checkpoint


def reconciliation_event(
    checkpoint: ExecutionCheckpoint,
    call_id: str,
    outcome: Literal["applied", "not_applied"],
    output: Optional[str] = None,
) -> TraceEvent:
    """Create the durable fact that settles one crash-ambiguous effect.

    ``applied`` means a human or external reconciler verified the effect occurred;
    ``not_applied`` means it verified that it did not. The latter is recorded as a
    failed call result so the resumed model can decide whether to issue a new call—this
    function never retries an uncertain mutation itself.
    """
    match = next(
        (call for call in checkpoint.unsettled_calls if call.call_id == call_id),
        None,
    )
    if match is None:
        raise ValueError(f"unsettled call not found in checkpoint: {call_id}")
    if outcome not in ("applied", "not_applied"):
        raise ValueError("outcome must be 'applied' or 'not_applied'")
    success = outcome == "applied"
    result = output or (
        "Effect was externally confirmed as already applied after interruption."
        if success else
        "Effect was externally confirmed as not applied after interruption."
    )
    if match.action_type == "model_effect":
        return TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=checkpoint.session_id,
            task_id=checkpoint.task_id,
            agent_name=checkpoint.agent_name,
            step_number=match.step_number,
            success=success,
            output=result,
            message=result,
            ignorable=False,
            metadata={
                "type": "responses_background_effect",
                "phase": "result",
                "operation": match.action_name.rsplit(".", 1)[-1],
                "request_snapshot_id": match.call_id,
                "reconciliation": {"outcome": outcome, "authority": "human"},
            },
        )

    event_type = (
        TraceEventType.SKILL_CALL
        if match.action_type == "skill" else TraceEventType.TOOL_CALL
    )
    return TraceEvent(
        event_type=event_type,
        session_id=checkpoint.session_id,
        task_id=checkpoint.task_id,
        agent_name=checkpoint.agent_name,
        step_number=match.step_number,
        action_index=match.action_index,
        action_type=match.action_type,
        action_name=match.action_name,
        output=result,
        message=result,
        success=success,
        error=None if success else result,
        metadata={
            "call_id": match.call_id,
            "success": success,
            "reconciliation": {"outcome": outcome, "authority": "human"},
        },
    )


__all__ = [
    "EXECUTION_CHECKPOINT_VERSION",
    "EffectReceipt",
    "ExecutionCheckpoint",
    "UnsettledCall",
    "derive_execution_checkpoint",
    "reconciliation_event",
]
