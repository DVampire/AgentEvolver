"""Escalate tool — a blocked sub-agent asks its parent MetaAgent for guidance.

This is the *send* side of the escalation channel: the tool calls
the kernel: the child reports the blocker to its parent and waits at a safe point until
the parent answers, with ``reply_tool`` or from its own ``on_event``.

Only meaningful for a sub-agent dispatched by a parent (it needs one to ask). Run
standalone, it reports that there is no parent to escalate to.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Ask the parent MetaAgent for guidance when blocked, then continue with its reply."

_GUIDANCE = """
Escalate to the parent MetaAgent when you are blocked and cannot proceed on your own, and get back concrete guidance. Use it instead of failing silently or guessing when: a capability you need is missing, the task is ambiguous or under-specified, you hit a blocker outside your scope, or you've failed the same way twice.

- Only works when you were dispatched by a MetaAgent (there must be a parent to ask); otherwise it returns that there is no parent.
- The call blocks until the MetaAgent replies (or a timeout). Treat the returned guidance as an instruction and act on it; if told to stop, stop gracefully.
- Escalate sparingly — only for real blockers, not routine decisions you can make yourself.
"""

_EXAMPLES = [
    '{"name": "escalate_tool", "args": {"reason": "Need to deploy the site but no deploy capability is available", "situation": "Built the static site at /work/site but there is no tool to serve it at a URL", "suggestion": "Generate or enable a deployment tool"}}',
]


@TOOL.register_module(force=True)
class EscalateTool(Tool):
    """Ask the parent MetaAgent for guidance when blocked (fires the escalation protocol)."""

    name: str = "escalate_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Only asks the parent for guidance; mutates nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, reason: str, situation: str = "", suggestion: str = "", **kwargs) -> Response:
        """Hand the run back to whoever dispatched it, blocked.

        Args:
            reason: One line — why you are blocked / what you need.
            situation: What you tried and what happened (evidence).
            suggestion: What you think should happen (e.g. "a deploy tool needs to be
                generated").
        """
        ctx = kwargs.get("ctx")
        try:
            guidance = await _ask_parent(ctx, reason, situation, suggestion)
            return Response(type=ResponseType.TOOL, success=True, message=guidance)
        except Exception as e:
            logger.error(f"| ❌ escalate_tool failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Escalation failed: {e}")


#: How long a blocked child waits for its parent. Long, because the parent may be
#: mid-turn on something else; a child that gives up early has to guess instead.
ESCALATION_TIMEOUT_S = 300.0


async def _ask_parent(ctx, reason: str, situation: str, suggestion: str) -> str:
    """Report the blocker to the parent process and wait at a safe point for its reply.

    The whole mechanism, with nothing between the tool and the kernel: a blocked child
    *is* a process waiting for a message, so there is no suspension registry to key and
    nothing to resolve.
    """
    from agentevolver.runtime import kernel

    extra = getattr(ctx, "extra", None) or {}
    process = kernel.get(str(extra.get("process_pid") or ""))
    if process is None or not process.parent_pid:
        return ("No parent to escalate to (running standalone). Proceed on your own or "
                "stop gracefully.")
    text = "\n".join(part for part in (
        f"Blocked: {reason}",
        f"Situation: {situation}" if situation else "",
        f"Suggestion: {suggestion}" if suggestion else "",
    ) if part)
    # Announced before the wait, not after it: the point of observing an escalation is
    # to see that a child is parked, and a run that dies waiting would otherwise leave
    # no record that it ever asked.
    await _announce_escalation(process, reason)
    answer = await process.ask_parent(text, timeout=ESCALATION_TIMEOUT_S)
    return answer or ("Parent did not respond in time. Please stop the current subtask "
                      "gracefully.")


async def _announce_escalation(process, reason: str) -> None:
    """Tell observers a child is blocked. Never raises: an observer must not block it."""
    from agentevolver.agent.loop.events import events
    from agentevolver.hook.types import HookEvent

    await events.broadcast(
        HookEvent.ON_ESCALATE,
        {
            "agent_name": process.name,
            "task_id": process.pid,
            "session_id": process.session_id,
            "parent_session_id": process.parent_pid,
            "reason": reason,
            "timeout_s": ESCALATION_TIMEOUT_S,
        },
        ctx=process.ctx,
    )
