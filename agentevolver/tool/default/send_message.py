"""send_message tool — give a live background sub-agent more work.

The parent's write end of a continuable child's mailbox. `reply_tool` is the other one,
and they are not interchangeable: a reply answers a child that is *suspended* inside a
step it was already permitted to take, while this hands a live child a fresh task whose
effects are whatever that child then does. That difference is why this tool declares that
it mutates and `reply_tool` does not — plan mode refuses an agent dispatch outright for
exactly the same reason, and sending new work to a child is one.

Delivery is a queue, not an interrupt. The message becomes the child's next turn; a child
mid-turn finishes that turn first. See `agentevolver/subagent/README.md`.
"""

from typing import Any, Dict

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Give a continuable background sub-agent more work, on the same conversation."

_INSTRUCTION = """
## Function
Send a message to a background sub-agent you started with `continuable: true`. It becomes
that sub-agent's next turn and continues the same conversation, so you do not have to
repeat what you already told it.

This returns confirmation that the message was delivered, not an answer. Collect the
answer with `job_output_tool` once the turn finishes.

## Parameters
- job_id (str, required): the id you were given when you backgrounded the sub-agent.
- message (str, required): what to tell it. Self-contained enough to act on, but it still
  remembers the earlier turns.

## Guidance
- Only a continuable sub-agent can take one. A one-shot child answers once and ends;
  sending to it fails and says so.
- If it is mid-turn the message waits until that turn finishes — it cannot redirect work
  already underway. To stop what it is doing, use `job_kill_tool`.
- A failure means the message was NOT delivered. Do not assume it arrived.

## Example
{"name": "send_message_tool", "args": {"job_id": "job_1a2b3c4d", "message": "Now run the same check against the staging config and report what differs."}}
"""


@TOOL.register_module(force=True)
class SendMessageTool(Tool):
    """Queue another turn on a continuable background sub-agent."""

    name: str = "send_message_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    #: Its effects are the child's effects, which are not knowable from here — the same
    #: reason the plan-mode gate refuses an agent dispatch. Declaring it read-only
    #: because "it only puts a string in a queue" would let a held run do anything at
    #: all through a child.
    permission_mode: str = Field(default="workspace_write", description="Starts work in another agent; its effects are whatever that agent does.")
    mutates: bool = Field(default=True, description="Hands a live sub-agent a new task.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, job_id: str, message: str, **kwargs) -> Response:
        from agentevolver.subagent import subagent_manager

        ctx = kwargs.get("ctx")
        session_id = str(getattr(ctx, "id", "") or "")
        try:
            when = await subagent_manager.send(job_id, message, session_id=session_id)
        except ValueError as error:
            # Not delivered. Returned as a failure rather than a message, so the model
            # cannot read it as an acknowledgement and go on waiting for a turn that
            # will never start.
            return Response(type=ResponseType.TOOL, success=False, message=str(error))
        except Exception as error:                                  # noqa: BLE001
            logger.error(f"| ❌ send_message_tool failed: {error}")
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"The message was NOT delivered to {job_id}: {error}")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Delivered to {job_id}. {when}\nIt does not answer here — read the "
                     f'result with job_output_tool(job_id="{job_id}").'),
            data={"job_id": job_id},
        )
