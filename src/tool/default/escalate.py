"""Escalate tool — a blocked sub-agent asks its parent MetaAgent for guidance.

This is the *sending* side of the escalation protocol. The receiving side (MetaAgent
handling EscalationMessage / escalation_replies) and the transport (`escalation_hook`
→ runtime_manager.ask the parent) were already built; this tool is the trigger the
sub-agent invokes when it is stuck, which fires the hook and returns MetaAgent's reply.

Only meaningful for a sub-agent dispatched by MetaAgent (it needs a parent to ask). Run
standalone, it reports that there is no parent to escalate to.
"""

from typing import Any, Dict

from pydantic import Field

from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.hook import hook_manager
from src.hook.types import HookContext
from src.logger import logger
from src.registry import TOOL

_DESCRIPTION = "Ask the parent MetaAgent for guidance when blocked, then continue with its reply."

_INSTRUCTION = """
## Function
Escalate to the parent MetaAgent when you are blocked and cannot proceed on your own, and get back concrete guidance. Use it instead of failing silently or guessing when: a capability you need is missing, the task is ambiguous or under-specified, you hit a blocker outside your scope, or you've failed the same way twice.

## Parameters
- reason (str, required): one line — why you are blocked / what you need.
- situation (str, optional): what you tried and what happened (evidence).
- suggestion (str, optional): what you think should happen (e.g. "a deploy tool needs to be generated").

## Guidance
- Only works when you were dispatched by a MetaAgent (there must be a parent to ask); otherwise it returns that there is no parent.
- The call blocks until the MetaAgent replies (or a timeout). Treat the returned guidance as an instruction and act on it; if told to stop, stop gracefully.
- Escalate sparingly — only for real blockers, not routine decisions you can make yourself.

## Example
{"name": "escalate_tool", "args": {"reason": "Need to deploy the site but no deploy capability is available", "situation": "Built the static site at /work/site but there is no tool to serve it at a URL", "suggestion": "Generate or enable a deployment tool"}}
"""


@TOOL.register_module(force=True)
class EscalateTool(Tool):
    """Ask the parent MetaAgent for guidance when blocked (fires the escalation protocol)."""

    name: str = "escalate_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Only asks the parent for guidance; mutates nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, reason: str, situation: str = "", suggestion: str = "", **kwargs) -> Response:
        ctx = kwargs.get("ctx")
        parent_session_id = getattr(ctx, "parent_session_id", None)
        if not parent_session_id:
            return Response(
                type=ResponseType.TOOL, success=False,
                message="No parent MetaAgent to escalate to (running standalone). Proceed on your own or stop gracefully.",
            )
        task_id = getattr(ctx, "subtask_id", None) or getattr(ctx, "id", "")
        agent_name = getattr(ctx, "name", "") or ""
        try:
            result = await hook_manager(
                name="escalation_hook",
                input={
                    "parent_session_id": parent_session_id,
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "reason": reason,
                    "situation": situation,
                    "suggestion": suggestion,
                },
                ctx=HookContext(id=getattr(ctx, "id", "") or task_id, name="escalation_hook"),
            )
            guidance = getattr(result, "additional_context", None) or "No guidance returned; use your best judgement or stop gracefully."
            logger.info(f"| 🆘 escalate_tool: '{agent_name}' escalated → guidance received")
            return Response(type=ResponseType.TOOL, success=True, message=guidance)
        except Exception as e:
            logger.error(f"| ❌ escalate_tool failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Escalation failed: {e}")
