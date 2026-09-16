"""send_message tool — give a live background sub-agent more work.

The parent's write end of a continuable child's mailbox. `reply_tool` is the other one,
and they are not interchangeable: a reply answers a child that is *suspended* inside a
step it was already permitted to take, while this hands a live child a fresh task whose
effects are whatever that child then does. That difference is why this tool declares that
it mutates and `reply_tool` does not — plan mode refuses an agent dispatch outright for
exactly the same reason, and sending new work to a child is one.

Delivery is a queue, not an interrupt. The message becomes the child's next turn; a child
mid-turn finishes that turn first. See `agentevolver/runtime/README.md`.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Give a continuable background sub-agent more work, on the same conversation."

_GUIDANCE = """
Send a message to a background sub-agent you started with `continuable: true`. It becomes
that sub-agent's next turn and continues the same conversation, so you do not have to
repeat what you already told it.

This returns confirmation that the message was delivered, not an answer. Collect the
answer with `job__output` once the turn finishes.

- Only a continuable sub-agent can take one. A one-shot child answers once and ends;
  sending to it fails and says so.
- If it is mid-turn the message waits until that turn finishes — it cannot redirect work
  already underway. To stop what it is doing, use `job__kill`.
- A failure means the message was NOT delivered. Do not assume it arrived.
"""

_EXAMPLES = [
    '{"name": "send_message_tool", "args": {"job_id": "job_1a2b3c4d", "message": "Now run the same check against the staging config and report what differs."}}',
]


@TOOL.register_module(force=True)
class SendMessageTool(Tool):
    """Queue another turn on a continuable background sub-agent."""

    name: str = "send_message_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
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
        """Send a message to a running background sub-agent.

        Args:
            job_id: The id you were given when you backgrounded the sub-agent.
            message: What to tell it. Self-contained enough to act on, but it still
                remembers the earlier turns.
        """
        # `job_id` is the child's pid: what a dispatch returned and what its reports
        # are signed with. It used to be looked up in the old job registry, which no
        # kernel child appears in, so nothing could be reached.
        from agentevolver.runtime import kernel
        from agentevolver.runtime.envelopes import TaskEnvelope

        child = kernel.get(str(job_id))
        try:
            # An undelivered message comes back as an unsuccessful Response, not as an
            # exception: the model must not be able to read "not delivered" as an
            # acknowledgement and go on waiting for a turn that will never start.
            if child is None or not child.alive:
                delivered = False
            elif not child.resident:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(f"{job_id} answers once and is finished, so it cannot take "
                             "more work. Dispatch it again, or start it as a resident "
                             "child if you mean to keep talking to it."),
                )
            else:
                delivered = await kernel.send(child, TaskEnvelope(task=message))
        except Exception as error:                                  # noqa: BLE001
            logger.error(f"| ❌ send_message_tool failed: {error}")
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"The message was NOT delivered to {job_id}: {error}")
        if not delivered:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"No live sub-agent answers to {job_id}; it has finished or was "
                         "never started. Nothing was delivered."),
            )
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Delivered to {job_id}. It runs the message as its next turn; read "
                     "the result with job__output."),
            data={"pid": str(job_id), "queued": len(child.mailbox)},
        )
