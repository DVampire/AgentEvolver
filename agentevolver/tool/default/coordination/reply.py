"""reply tool — an orchestrator answers a sub-agent that ESCALATED.

The *reply* side of the escalation channel, the mirror of ``escalate_tool``: the sub-agent's
``escalate_tool`` leaves the child waiting at a safe point; this delivers the parent's
answer as an ordinary message, which is what lets the child continue.
The pause/resume rendezvous is the runtime's suspend/resume channel, keyed by the
the child's pid, which a dispatch returned and its reports are signed with.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Reply to a sub-agent that ESCALATED (is blocked), unblocking it with concrete guidance."

_GUIDANCE = """
Answer a sub-agent that escalated (reported it is blocked) so it can continue. Use it whenever an ESCALATE event is pending: give a concrete, actionable instruction, or tell the sub-agent to stop gracefully.

- Reply promptly — the sub-agent is blocked, waiting, and holding up its round until you answer.
- Be specific: point at the capability/file/approach to use, or state the decision it was unsure about.
"""

_EXAMPLES = [
    '{"name": "reply_tool", "args": {"task_id": "subtask-1a2b3c", "reply": "Use write_file_tool to create the config at /work/app/config.json, then re-run the build."}}',
]


@TOOL.register_module(force=True)
class ReplyTool(Tool):
    """Reply to a blocked, escalated sub-agent (fires the reply side of the escalation protocol)."""

    name: str = "reply_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Only unblocks a sub-agent; mutates nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, task_id: str, reply: str, **kwargs) -> Response:
        """Answer a sub-agent that escalated.

        Args:
            task_id: The exact task_id from the ESCALATE event.
            reply: Concrete guidance for the blocked sub-agent (or an instruction to
                stop gracefully).
        """
        try:
            from agentevolver.runtime import kernel

            child = kernel.get(str(task_id))
            delivered = bool(child is not None and child.alive
                             and await kernel.reply(child, reply))
            msg = (f"Guidance delivered to sub-agent [{task_id}]." if delivered
                   else f"No sub-agent was waiting for [{task_id}] (already replied or timed out).")
            return Response(type=ResponseType.TOOL, success=True, message=msg)
        except Exception as e:
            logger.error(f"| ❌ reply_tool failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Reply failed: {e}")
