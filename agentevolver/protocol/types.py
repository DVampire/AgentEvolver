"""Protocol-specific envelopes and compatibility exports for Runtime controls.

Escalation and progress are protocol semantics. Control, query, and subscription messages
are Runtime types re-exported here for callers using the historical Protocol import path.
"""

from __future__ import annotations

from typing import Literal, Optional

from agentevolver.runtime.types import (
    BaseMessage,
    ControlMessage,
    QueryMessage,
    SubscriptionEventMessage,
)


class EscalationMessage(BaseMessage):
    """gate — posted to the parent when a sub-agent blocks; the context it replies from."""

    task_id: str
    agent_name: str = ""
    session_id: str = ""
    reason: str = ""
    situation: str = ""
    suggestion: str = ""

    @property
    def text(self) -> str:
        body = f"Reason: {self.reason}\nSituation: {self.situation}"
        if self.suggestion:
            body += f"\nSuggestion: {self.suggestion}"
        return body


class MonitorProgressMessage(BaseMessage):
    """tell — a periodic progress update about a running subprocess / sub-agent."""

    task_id: str
    agent_name: str = ""
    session_id: str = ""
    pid: int = 0
    status: Literal["running", "completed", "failed", "timeout"] = "running"
    elapsed: float = 0.0
    recent_output: str = ""
    exit_code: Optional[int] = None


__all__ = [
    "ControlMessage", "EscalationMessage", "MonitorProgressMessage", "QueryMessage",
    "SubscriptionEventMessage",
]
