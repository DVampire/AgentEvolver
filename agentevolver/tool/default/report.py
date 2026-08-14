"""report tool — a sub-agent tells its parent something before it is finished.

The child's write end of the same transcript its result lands in: the job the parent is
holding for it. Not a second channel — `job_output_tool` reads reports and results in the
order the child produced them, so a parent collecting a child has one thing to read.

Distinct from `escalate_tool`, which is a rendezvous: escalating suspends the child until
the parent answers, and this does not stop it at all. A child that reports and keeps
working has said something; a child that escalates has stopped and needs something.
"""

from typing import Any, Dict

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Report a finding or result to the agent that started you, without waiting for a reply."

_INSTRUCTION = """
## Function
Tell the agent that dispatched you something it can act on, while you keep working. Use it
for a finding that changes what that agent should do next, and for progress on a long job
so it is not left guessing whether you are stuck.

That agent does not see your transcript, your tool output or your reasoning. Finishing
your work is not itself a result — only what you report and what you finally return
reach it.

## Parameters
- output (str, required): actionable content. Summarize the conclusion and name the
  shared paths you touched; do not paste transcripts.

## Guidance
- This does not end your turn and does not finish your task. Keep going afterwards, and
  still call `done_tool` with your final result.
- It expects no reply. If you are blocked and need an answer before you can continue,
  use `escalate_tool` instead — that one waits.
- Only the agent that started you receives it.
- Running standalone, with nobody above you, this says so and changes nothing.

## Example
{"name": "report_tool", "args": {"output": "The failing test is a fixture problem, not a parser bug: tests/fixtures/log.json has the events in reverse order. Fixed the fixture at that path; the parser is unchanged."}}
"""


@TOOL.register_module(force=True)
class ReportTool(Tool):
    """Append a sub-agent's own words to the transcript its parent collects."""

    name: str = "report_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    #: Says something; changes nothing a person or a later run could observe. A child
    #: held in plan mode may still report what it found — that is the one thing a
    #: planning run wants out of it.
    permission_mode: str = Field(default="read_only", description="Writes to the parent's record of this sub-agent; touches nothing else.")
    mutates: bool = Field(default=False, description="Reports; changes no state.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, output: str, **kwargs) -> Response:
        from agentevolver.subagent import subagent_manager

        ctx = kwargs.get("ctx")
        extra = getattr(ctx, "extra", None) or {}
        job_id = extra.get("report_job_id")
        if not job_id:
            # A top-level run has no parent to report to. Said plainly and as a success:
            # the agent asked for something reasonable and nothing went wrong, and a
            # failure here would push it into retrying a call that cannot work.
            return Response(
                type=ResponseType.TOOL, success=True,
                message=("Nobody dispatched you, so there is no parent to report to. Put "
                         "this in your final result instead."),
            )
        try:
            # The name is read from the context rather than from ``ctx.name``: by the
            # time a tool is called the context has been converted, and ``name`` on the
            # converted one is the tool's, not the agent's.
            accepted = subagent_manager.report(
                job_id, output, agent_name=str(extra.get("report_agent_name") or ""))
        except Exception as error:                                  # noqa: BLE001
            logger.error(f"| ❌ report_tool failed: {error}")
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Could not report: {error}")
        if not accepted:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=("The agent that started you is no longer holding a record for "
                         "you, so this did not reach it. Carry on and put it in your "
                         "final result."),
            )
        return Response(
            type=ResponseType.TOOL, success=True,
            message="Reported. This did not end your turn — carry on, and still finish "
                    "with done_tool.",
            data={"job_id": job_id},
        )
