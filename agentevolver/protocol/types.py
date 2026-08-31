"""Protocol message types — the typed envelopes carried over the runtime's channels.

Each is a ``runtime.BaseMessage`` (so it flows through an agent's inbox and can carry a
reply_future). The conversation logic that sends/answers them lives in ``server.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from agentevolver.runtime import BaseMessage, TaskMessage


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


class ControlMessage(BaseMessage):
    """tell — a cancel / pause / resume instruction to a running agent."""

    action: Literal["cancel", "pause", "resume"]
    reason: str = ""


class QueryMessage(BaseMessage):
    """ask — a request for a running agent's status snapshot (empty ``fields`` = full)."""

    fields: Optional[List[str]] = None


class SubscriptionEventMessage(TaskMessage):
    """Typed publish/subscribe event that starts one queued subscriber turn.

    It subclasses :class:`TaskMessage` deliberately: a subscribed Agent handles an event
    through the same on_start → act → on_end lifecycle as any other turn.  Runtime still
    distinguishes it by type so it can prepend the subscriber's standing brief, attach
    the subscriber's files/context, and keep published work on the continuable driver's
    serial task queue rather than racing the live inbox.
    """

    topic: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    publisher: str = ""
    published_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        topic: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        publisher: str = "",
    ) -> "SubscriptionEventMessage":
        body = dict(payload or {})
        published_at = datetime.now(timezone.utc).isoformat()
        task = "\n".join(
            [
                "A subscribed event was published to this agent.",
                f"Topic: {topic}",
                f"Event type: {event_type}",
                f"Publisher: {publisher or '(unspecified)'}",
                f"Published at: {published_at}",
                "Payload:",
                json.dumps(body, ensure_ascii=False, indent=2, default=str),
                "Handle this event according to your standing subscriber brief.",
            ]
        )
        return cls(
            task=task,
            topic=topic,
            event_type=event_type,
            payload=body,
            publisher=publisher,
            published_at=published_at,
        )
