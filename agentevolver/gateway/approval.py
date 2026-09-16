"""Session-scoped, one-shot human approval for Tool policy ``ASK`` decisions.

The Tool pipeline owns the decision to ask; the Gateway owns the human channel.  This
module is the rendezvous between them.  A request is immutable, listable after a client
reconnects, consumable exactly once, and bounded by a timeout.  Missing, stale, duplicate,
or cross-session responses never grant authority.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.tool.execution import ToolExecution
from agentevolver.utils import make_id


DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300.0

ApprovalPublisher = Callable[
    [str, Dict[str, Any], "PendingToolApproval"], Awaitable[None]
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PendingToolApproval:
    """The frozen call a person is being asked to authorize once."""

    execution: ToolExecution
    reason: str
    timeout_seconds: float
    id: str = field(default_factory=make_id)
    requested_at: datetime = field(default_factory=_utc_now)
    future: asyncio.Future[bool] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.future = asyncio.get_running_loop().create_future()

    @property
    def expires_at(self) -> datetime:
        return self.requested_at + timedelta(seconds=self.timeout_seconds)

    def public(self) -> Dict[str, Any]:
        """Safe client payload: enough to identify the act, without argument values.

        The guard's reason is the human-readable approval statement. Raw arguments may
        contain file contents, credentials, cookies, or private prompts, so the protocol
        exposes their names and a digest that binds the dialog to the immutable call.
        """
        execution = self.execution
        return {
            "approval_id": self.id,
            "summary": self.reason or f"Approve one call to {execution.tool_name}?",
            "reason": self.reason,
            "session_id": execution.project_id,
            "conversation_id": (
                execution.session_id
                if execution.session_id != execution.project_id else None
            ),
            "task_id": execution.task_id,
            "agent_name": execution.agent_name,
            "step_number": execution.step_number,
            "action_index": execution.action_index,
            "call_id": execution.call_id,
            "root_call_id": execution.root_call_id,
            "parent_call_id": execution.parent_call_id,
            "tool_name": execution.tool_name,
            "tool_version": execution.tool_version,
            "argument_names": sorted(execution.arguments),
            "arguments_sha256": hashlib.sha256(
                execution.arguments_json.encode("utf-8")
            ).hexdigest(),
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "timeout_seconds": self.timeout_seconds,
        }


class GatewayApprovalRendezvous:
    """Hold Tool approvals until a Gateway client accepts, rejects, or times out."""

    def __init__(
        self,
        publisher: ApprovalPublisher,
        *,
        timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        timeout_seconds = float(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("approval timeout must be greater than zero")
        self._publisher = publisher
        self._timeout_seconds = timeout_seconds
        self._pending: Dict[str, PendingToolApproval] = {}
        # All state transitions run on one event loop, but they cross await points while
        # publishing. The lock prevents timeout and a response from both settling a call.
        self._lock = asyncio.Lock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def set_timeout(self, timeout_seconds: float) -> None:
        value = float(timeout_seconds)
        if value <= 0:
            raise ValueError("approval timeout must be greater than zero")
        self._timeout_seconds = value

    async def request(self, execution: ToolExecution, reason: str) -> bool:
        """Publish one request and wait for its exact response; default is deny."""
        if not execution.session_id:
            raise RuntimeError("Tool approval requires a real session id")
        record = PendingToolApproval(
            execution=execution,
            reason=str(reason or "").strip(),
            timeout_seconds=self._timeout_seconds,
        )
        async with self._lock:
            self._pending[record.id] = record
            try:
                await self._publisher("approval.requested", record.public(), record)
            except Exception:
                self._pending.pop(record.id, None)
                raise
        logger.info(
            f"| ✋ Approval [{record.id}] requested for "
            f"{execution.tool_name} in project {execution.project_id}"
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(record.future), timeout=record.timeout_seconds,
            )
        except asyncio.TimeoutError:
            async with self._lock:
                # A response may have completed while this coroutine was waiting for the
                # lock after its timer fired. Completed consent wins; it is never changed
                # into a timeout merely because publishing took the final millisecond.
                if record.future.done():
                    return bool(record.future.result())
                if self._pending.pop(record.id, None) is record:
                    await self._publisher(
                        "approval.expired",
                        {**record.public(), "approved": False, "decision": "timeout"},
                        record,
                    )
                record.future.set_result(False)
            logger.info(f"| ⏱️ Approval [{record.id}] expired; call denied")
            return False
        except asyncio.CancelledError:
            async with self._lock:
                self._pending.pop(record.id, None)
                if not record.future.done():
                    record.future.set_result(False)
            raise
        finally:
            self._pending.pop(record.id, None)

    async def respond(
        self,
        *,
        session_id: str,
        approval_id: str,
        approved: bool,
        decision: str,
        comment: str = "",
    ) -> bool:
        """Settle one matching request. False means stale, duplicate, or wrong Session."""
        async with self._lock:
            record = self._pending.get(str(approval_id))
            if record is None or record.execution.project_id != str(session_id):
                return False
            if record.future.done():
                return False
            payload = {
                **record.public(),
                "approved": bool(approved),
                "decision": str(decision),
                "comment": str(comment),
            }
            # Publish before waking the Tool pipeline. Its post-approval checkpoint then
            # flushes this fact before the body can change the world.
            await self._publisher("approval.responded", payload, record)
            self._pending.pop(record.id, None)
            record.future.set_result(bool(approved))
        logger.info(
            f"| {'✅' if approved else '🚫'} Approval [{record.id}] "
            f"{'accepted' if approved else 'rejected'}"
        )
        return True

    def pending(self, session_id: str) -> List[PendingToolApproval]:
        """Outstanding requests for one Session, oldest first."""
        return sorted(
            (
                record for record in self._pending.values()
                if record.execution.project_id == str(session_id)
                and not record.future.done()
            ),
            key=lambda record: record.requested_at,
        )

    async def cancel_all(self, reason: str = "gateway_stopped") -> None:
        """Deny every waiter during shutdown; no suspended Tool outlives its channel."""
        async with self._lock:
            records = list(self._pending.values())
            self._pending.clear()
            for record in records:
                if record.future.done():
                    continue
                try:
                    await self._publisher(
                        "approval.cancelled",
                        {**record.public(), "approved": False, "decision": reason},
                        record,
                    )
                except Exception as exc:  # noqa: BLE001 - shutdown must still release waiters
                    logger.warning(
                        f"| ⚠️ Could not publish cancellation for approval "
                        f"[{record.id}]: {exc}"
                    )
                record.future.set_result(False)


__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "PendingToolApproval",
    "GatewayApprovalRendezvous",
]
