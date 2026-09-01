"""Publish a typed event to session-scoped, continuable Agent subscribers."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool


_DESCRIPTION = (
    "Publish one typed event to every live Agent subscribed to a logical topic in this "
    "task tree; each delivery becomes a serialized subscriber turn."
)

_GUIDANCE = """
Use this after starting continuable background agents with `subscription_topics`.

- `topic` is the logical name used at subscription time. The protocol automatically
  scopes it to this root session, so another run cannot receive the event.
- `event_type` says what happened, such as `website.release.ready`.
- `payload` is the structured data subscribers need to act. Prefer stable IDs and URLs;
  do not put secrets or another subscriber's private context in a broadcast.
- The result includes `fanout`. Treat zero as a failed coordination step and check topic
  spelling/subscriber lifecycle. When an exact subscriber count is expected, verify it.
- Publishing queues work; it does not wait for subscriber results. Read each subscriber's
  `job__output` and state before publishing a later dependent event.
"""

_EXAMPLES = [
    '{"name": "publish_event_tool", "args": {"topic": "website.releases", '
    '"event_type": "website.release.ready", "payload": {"iteration_id": "V0", '
    '"url": "http://localhost:8000"}}}',
]


@TOOL.register_module(force=True)
class PublishEventTool(Tool):
    """LLM-facing publish operation over the Protocol/Runtime subscription channel."""

    name: str = "publish_event_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = False
    permission_mode: str = "workspace_write"
    # A publish starts subscriber work, whose effects are not knowable here.
    mutates: bool = True

    async def __call__(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        **kwargs: Any,
    ) -> Response:
        """Publish a typed event to all live subscribers of a logical topic.

        Args:
            topic: Logical topic name registered by continuable background agents.
            event_type: Stable event kind describing what subscribers should handle.
            payload: Structured event data shared with every matching subscriber.
            **kwargs: Runtime-injected values, including the active Agent context.

        Returns:
            A tool response containing the event ID, scoped fan-out, and topic.
        """
        from agentevolver.protocol import protocol_manager

        ctx = kwargs.get("ctx")
        if ctx is None:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message="publish_event_tool requires an active Agent session context.",
            )
        event_type_name = str(event_type or "").strip()
        if not event_type_name:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message="event_type must be non-empty.",
            )
        try:
            publisher = str(getattr(ctx, "name", "") or "")
            sent, scoped, event = await protocol_manager.publish_event(
                topic,
                event_type=event_type_name,
                payload=dict(payload or {}),
                ctx=ctx,
                publisher=publisher,
            )
        except Exception as error:  # noqa: BLE001
            logger.error(f"| ❌ publish_event_tool failed: {error}")
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"Event was not published: {error}",
            )

        visible_topic = scoped.split("::", 1)[-1]
        return Response(
            type=ResponseType.TOOL,
            success=sent > 0,
            message=(
                f"Published {event_type_name!r} on {visible_topic!r} to {sent} subscriber(s)."
                if sent
                else f"Published to zero subscribers on {visible_topic!r}; check registration."
            ),
            data={
                "event_id": event.id,
                "event_type": event_type_name,
                "topic": visible_topic,
                "fanout": sent,
            },
        )
