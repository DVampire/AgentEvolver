"""Authoritative execution pipeline shared by native and Code Mode tool calls.

The model-facing agent loop decides *which* capability to invoke. Once the route is a
Tool, this module owns the rest of the call: immutable identity, argument rejection,
monotonic policy, timeout, exception/result normalization, post-processing, and the
final execution record consumed by Trace. Keeping those phases together prevents a new
caller (Workflow, Code Mode, or a future SDK) from accidentally inventing a weaker tool
path.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from agentevolver.logger import logger
from agentevolver.response.types import Response, ResponseType
from agentevolver.utils import make_id


TOOL_EXECUTION_SCHEMA_VERSION = 2


class ToolExecutionStage(str, Enum):
    """The phase that produced the final result."""

    RESOLVE = "resolve"
    VALIDATE = "validate"
    GUARD = "guard"
    EXECUTE = "execute"
    POST_EXECUTE = "post_execute"
    FINALIZE = "finalize"


class ToolErrorCode(str, Enum):
    """Stable failure vocabulary for traces, datasets, UIs, and retry policy."""

    NOT_FOUND = "not_found"
    INVALID_ARGUMENTS = "invalid_arguments"
    POLICY_DENIED = "policy_denied"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    APPROVAL_DENIED = "approval_denied"
    GUARD_ERROR = "guard_error"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    TOOL_REPORTED_ERROR = "tool_reported_error"
    INVALID_RESULT = "invalid_result"
    POSTPROCESS_ERROR = "postprocess_error"
    FINALIZATION_ERROR = "finalization_error"


class ToolPolicyKind(str, Enum):
    """A monotonic guard may abstain, deny, or request one-shot approval.

    There is deliberately no ``allow`` value. If two guards run, neither can undo the
    other's denial merely because it ran later.
    """

    ABSTAIN = "abstain"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class ToolPolicyDecision:
    """One guard's decision without an authority-expanding state."""

    kind: ToolPolicyKind = ToolPolicyKind.ABSTAIN
    reason: str = ""

    @classmethod
    def abstain(cls) -> "ToolPolicyDecision":
        return cls()

    @classmethod
    def deny(cls, reason: str) -> "ToolPolicyDecision":
        return cls(ToolPolicyKind.DENY, str(reason))

    @classmethod
    def ask(cls, reason: str) -> "ToolPolicyDecision":
        return cls(ToolPolicyKind.ASK, str(reason))


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


@dataclass(frozen=True)
class ToolExecution:
    """Identity-protected description of one tool call.

    Arguments are kept as canonical JSON rather than a mutable dict. Every reader gets
    a fresh decoded copy, so one guard cannot rewrite what a later guard or the tool body
    sees. ``token`` is registry-owned correlation identity; callers choose ``call_id``
    but cannot choose or mutate the token.
    """

    schema_version: int
    token: str
    call_id: str
    root_call_id: str
    parent_call_id: Optional[str]
    session_id: str
    project_id: str
    task_id: Optional[str]
    agent_name: Optional[str]
    step_number: Optional[int]
    action_index: Optional[int]
    tool_name: str
    tool_version: str
    arguments_json: str

    @classmethod
    def create(
        cls,
        *,
        name: str,
        version: str,
        arguments: Dict[str, Any],
        ctx: Any = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> "ToolExecution":
        supplied = dict(context or {})
        extra = dict(getattr(ctx, "extra", {}) or {})
        call_id = str(supplied.get("call_id") or make_id())
        parent_call_id = supplied.get("parent_call_id")
        parent_call_id = str(parent_call_id) if parent_call_id else None
        root_call_id = str(supplied.get("root_call_id") or parent_call_id or call_id)
        session_id = str(
            supplied.get("session_id") or getattr(ctx, "id", "") or ""
        )
        return cls(
            schema_version=TOOL_EXECUTION_SCHEMA_VERSION,
            token=make_id(),
            call_id=call_id,
            root_call_id=root_call_id,
            parent_call_id=parent_call_id,
            session_id=session_id,
            project_id=str(
                supplied.get("project_id") or extra.get("project_id") or session_id
            ),
            task_id=_optional_string(supplied.get("task_id") or extra.get("task_id")),
            agent_name=_optional_string(
                supplied.get("agent_name") or getattr(ctx, "name", None)
            ),
            step_number=_optional_int(supplied.get("step_number")),
            action_index=_optional_int(supplied.get("action_index")),
            tool_name=str(name),
            tool_version=str(version or ""),
            arguments_json=json.dumps(
                arguments or {}, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=_json_default,
            ),
        )

    @property
    def arguments(self) -> Dict[str, Any]:
        """A detached argument snapshot; mutating it cannot change this execution."""
        return json.loads(self.arguments_json)

    def identity_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "token": self.token,
            "call_id": self.call_id,
            "root_call_id": self.root_call_id,
            "parent_call_id": self.parent_call_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "step_number": self.step_number,
            "action_index": self.action_index,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
        }


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Frozen summary attached to the public Response and durable Trace event."""

    schema_version: int
    success: bool
    stage: ToolExecutionStage
    duration_ms: float
    timeout_seconds: Optional[float]
    error_code: Optional[ToolErrorCode] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "success": self.success,
            "stage": self.stage.value,
            "duration_ms": self.duration_ms,
            "timeout_seconds": self.timeout_seconds,
            "error_code": self.error_code.value if self.error_code else None,
        }


GuardResult = Union[None, str, ToolPolicyDecision]
ToolGuard = Callable[[ToolExecution], Union[GuardResult, Awaitable[GuardResult]]]
ToolPostprocessor = Callable[
    [ToolExecution, Response], Union[Optional[Response], Awaitable[Optional[Response]]]
]
ToolResultObserver = Callable[
    [ToolExecution, ToolExecutionOutcome, Response], Union[None, Awaitable[None]]
]
ApprovalResolver = Callable[
    [ToolExecution, str], Union[bool, Awaitable[bool]]
]
BeforeInvoke = Callable[[], Union[None, Awaitable[None]]]


class ToolExecutionPipeline:
    """Ordered, extensible, fail-closed execution funnel for one Tool registry."""

    def __init__(self) -> None:
        self._guards: List[ToolGuard] = []
        self._postprocessors: List[ToolPostprocessor] = []
        self._observers: List[ToolResultObserver] = []
        self._approval_resolver: Optional[ApprovalResolver] = None

    def guard(self, guard: ToolGuard) -> Callable[[], None]:
        """Register a monotonic guard and return its exact disposer."""
        self._guards.append(guard)
        return lambda: self._remove_identity(self._guards, guard)

    def postprocess(self, processor: ToolPostprocessor) -> Callable[[], None]:
        """Register an ordered result processor and return its disposer."""
        self._postprocessors.append(processor)
        return lambda: self._remove_identity(self._postprocessors, processor)

    def observe(self, observer: ToolResultObserver) -> Callable[[], None]:
        """Observe the final authoritative result; observer failures are contained."""
        self._observers.append(observer)
        return lambda: self._remove_identity(self._observers, observer)

    def set_approval_resolver(
        self, resolver: Optional[ApprovalResolver],
    ) -> Callable[[], None]:
        """Set the one-shot approval seam and return an identity-safe disposer.

        ``None`` makes every ASK fail closed. The disposer clears only the resolver this
        call installed, so stopping an old Gateway cannot remove a newer integration.
        """
        self._approval_resolver = resolver
        return lambda: self._clear_approval_resolver(resolver)

    async def execute(
        self,
        execution: ToolExecution,
        invoke: Callable[[], Awaitable[Any]],
        *,
        timeout: Optional[float],
        preflight_error: Optional[Tuple[ToolErrorCode, str]] = None,
        initial_denials: Optional[List[str]] = None,
        call_guards: Optional[List[ToolGuard]] = None,
        before_invoke: Optional[BeforeInvoke] = None,
        finalize: Optional[Callable[[Response], Awaitable[Response]]] = None,
    ) -> Response:
        """Run every phase once and always return a classified Tool Response.

        Cancellation is the sole exception: ``CancelledError`` is re-raised so a caller
        stopping a session cannot be converted into a normal model observation.
        """
        started = time.monotonic()

        if preflight_error is not None:
            code, message = preflight_error
            stage = (ToolExecutionStage.RESOLVE if code == ToolErrorCode.NOT_FOUND
                     else ToolExecutionStage.VALIDATE)
            return await self._finish(
                execution, self._failure(message), started, timeout, stage, code,
            )

        denials = [str(reason) for reason in (initial_denials or []) if str(reason)]
        if denials:
            return await self._finish(
                execution, self._failure(denials[0]), started, timeout,
                ToolExecutionStage.GUARD, ToolErrorCode.POLICY_DENIED,
            )

        guarded = await self._run_guards(execution, call_guards=call_guards)
        if guarded is not None:
            code, reason = guarded
            return await self._finish(
                execution, self._failure(reason), started, timeout,
                ToolExecutionStage.GUARD, code,
            )

        # Guards and any one-shot human approval have now settled. This hook is where
        # callers put the final durability checkpoint: after the consent fact exists,
        # immediately before the only line that may enter the Tool body.
        if before_invoke is not None:
            prepared = before_invoke()
            if inspect.isawaitable(prepared):
                await prepared

        try:
            candidate = (
                await invoke() if timeout is None
                else await asyncio.wait_for(invoke(), timeout=timeout)
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            message = (
                f"Tool '{execution.tool_name}' execution timed out after {timeout} seconds. "
                "Re-issue it with narrower arguments, or split the work into steps that "
                "each finish inside that budget."
            )
            return await self._finish(
                execution, self._failure(message), started, timeout,
                ToolExecutionStage.EXECUTE, ToolErrorCode.TIMEOUT,
            )
        except Exception as error:  # noqa: BLE001 — the pipeline is the error boundary
            message = (
                f"Tool '{execution.tool_name}' raised {type(error).__name__}: {error}"
            )
            logger.error(f"| ❌ {message}")
            return await self._finish(
                execution, self._failure(message), started, timeout,
                ToolExecutionStage.EXECUTE, ToolErrorCode.EXECUTION_ERROR,
            )

        if not isinstance(candidate, Response):
            message = (
                f"Tool '{execution.tool_name}' returned {type(candidate).__name__}; "
                "tools must return agentevolver.response.Response."
            )
            return await self._finish(
                execution, self._failure(message), started, timeout,
                ToolExecutionStage.EXECUTE, ToolErrorCode.INVALID_RESULT,
            )

        response = candidate.model_copy(deep=True)
        error_code = None if response.success else ToolErrorCode.TOOL_REPORTED_ERROR
        try:
            response = await self._run_postprocessors(execution, response)
        except Exception as error:  # noqa: BLE001 — extensions cannot escape the funnel
            response = self._failure(
                f"Tool '{execution.tool_name}' post-processing failed: "
                f"{type(error).__name__}: {error}"
            )
            error_code = ToolErrorCode.POSTPROCESS_ERROR

        if finalize is not None:
            try:
                finalized = await finalize(response)
                if not isinstance(finalized, Response):
                    raise TypeError("finalizer must return Response")
                response = finalized
            except Exception as error:  # noqa: BLE001
                response = self._failure(
                    f"Tool '{execution.tool_name}' result finalization failed: "
                    f"{type(error).__name__}: {error}"
                )
                error_code = ToolErrorCode.FINALIZATION_ERROR

        if not response.success and error_code is None:
            error_code = ToolErrorCode.TOOL_REPORTED_ERROR
        return await self._finish(
            execution, response, started, timeout, ToolExecutionStage.FINALIZE,
            error_code,
        )

    async def _run_guards(
        self,
        execution: ToolExecution,
        *,
        call_guards: Optional[List[ToolGuard]] = None,
    ) -> Optional[Tuple[ToolErrorCode, str]]:
        # Call-local guards (notably the Tool's permission intent) precede extensions;
        # neither class can grant authority, so ordering never re-opens a refusal.
        for guard in (*tuple(call_guards or ()), *tuple(self._guards)):
            try:
                decision = guard(execution)
                if inspect.isawaitable(decision):
                    decision = await decision
            except Exception as error:  # fail closed: a broken guard is not permission
                return (
                    ToolErrorCode.GUARD_ERROR,
                    f"Tool policy guard failed closed: {type(error).__name__}: {error}",
                )
            if decision is None:
                continue
            if isinstance(decision, str):
                return ToolErrorCode.POLICY_DENIED, decision
            if not isinstance(decision, ToolPolicyDecision):
                return (
                    ToolErrorCode.GUARD_ERROR,
                    f"Tool policy guard returned unsupported decision "
                    f"{type(decision).__name__}; call denied.",
                )
            if decision.kind == ToolPolicyKind.ABSTAIN:
                continue
            if decision.kind == ToolPolicyKind.DENY:
                return ToolErrorCode.POLICY_DENIED, decision.reason or "Tool call denied."
            if self._approval_resolver is None:
                return (
                    ToolErrorCode.APPROVAL_UNAVAILABLE,
                    decision.reason or "Tool call requires approval, but no approval channel is available.",
                )
            try:
                accepted = self._approval_resolver(execution, decision.reason)
                if inspect.isawaitable(accepted):
                    accepted = await accepted
            except Exception as error:  # an unavailable/broken channel is never consent
                return (
                    ToolErrorCode.APPROVAL_UNAVAILABLE,
                    f"Approval could not be obtained: {type(error).__name__}: {error}",
                )
            if not accepted:
                return ToolErrorCode.APPROVAL_DENIED, decision.reason or "Tool call was not approved."
        return None

    def _clear_approval_resolver(
        self, resolver: Optional[ApprovalResolver],
    ) -> None:
        if self._approval_resolver is resolver:
            self._approval_resolver = None

    async def _run_postprocessors(
        self, execution: ToolExecution, response: Response,
    ) -> Response:
        current = response
        for processor in tuple(self._postprocessors):
            candidate = processor(execution, current.model_copy(deep=True))
            if inspect.isawaitable(candidate):
                candidate = await candidate
            if candidate is None:
                continue
            if not isinstance(candidate, Response):
                raise TypeError("tool postprocessor must return Response or None")
            # Post-processing may redact, enrich, or block. It cannot launder a failed
            # tool/policy result into success, which would make adding an extension widen
            # authority depending on listener order.
            if not current.success and candidate.success:
                raise ValueError("tool postprocessor cannot replace failure with success")
            current = candidate.model_copy(deep=True)
        return current

    async def _finish(
        self,
        execution: ToolExecution,
        response: Response,
        started: float,
        timeout: Optional[float],
        stage: ToolExecutionStage,
        error_code: Optional[ToolErrorCode],
    ) -> Response:
        duration_ms = (time.monotonic() - started) * 1000
        outcome = ToolExecutionOutcome(
            schema_version=TOOL_EXECUTION_SCHEMA_VERSION,
            success=bool(response.success),
            stage=stage,
            duration_ms=duration_ms,
            timeout_seconds=timeout,
            error_code=error_code,
        )
        settled = response.model_copy(deep=True)
        extra = dict(settled.extra or {})
        extra["execution"] = {**execution.identity_dict(), **outcome.to_dict()}
        settled.extra = extra
        for observer in tuple(self._observers):
            try:
                observed = observer(
                    execution, outcome, settled.model_copy(deep=True),
                )
                if inspect.isawaitable(observed):
                    await observed
            except Exception as error:  # observation must never change the outcome
                logger.warning(
                    f"| ⚠️ Tool result observer failed for {execution.tool_name}: {error}"
                )
        return settled

    @staticmethod
    def _failure(message: str) -> Response:
        return Response(type=ResponseType.TOOL, success=False, message=str(message))

    @staticmethod
    def _remove_identity(items: List[Any], target: Any) -> None:
        for index, item in enumerate(items):
            if item is target:
                del items[index]
                return


def _optional_string(value: Any) -> Optional[str]:
    return None if value is None or value == "" else str(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "TOOL_EXECUTION_SCHEMA_VERSION",
    "ToolExecutionStage",
    "ToolErrorCode",
    "ToolPolicyKind",
    "ToolPolicyDecision",
    "ToolExecution",
    "ToolExecutionOutcome",
    "ToolExecutionPipeline",
]
