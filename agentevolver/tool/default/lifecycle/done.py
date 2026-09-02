"""Done tool for indicating that the task has been completed."""
from typing import Any, Dict, List
from pydantic import Field
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.registry import TOOL

_DESCRIPTION = "Finish the task (or subtask) and return its result."

_GUIDANCE = """
- Call this to finish a task or subtask. `result` is the only thing the caller keeps: for a
  dispatched sub-agent it is ALL the orchestrator sees of your work — your steps, commands,
  and files are not visible to it — so a one-line "done" throws away everything it needs.
- Make `result` a handoff the caller can act on without redoing your work, scaled to the
  work itself. A trivial task needs a sentence. For a substantial deliverable, state: what
  you produced and WHERE (the paths/files), what you verified and HOW (the check you ran and
  its outcome), and what is unfinished, failed, or uncertain — the parts the next step must
  not assume are done.
- State outcomes, not a step-by-step narrative of how you got there. Keep the brief "why"
  in `reasoning`; put what was produced and its current state in `result`.
- Be honest about partial work: an accurate account of what is and is not done is worth far
  more to the caller than a result that reads as complete when it is not.
"""

_EXAMPLES = [
    '{"name": "done_tool", "args": {"reasoning": "All acceptance checks pass.", "result": "Implemented the CLI in src/main.rs and src/cmd/*.rs; compile.sh builds ./executable offline. Verified: --help, --version and the add/query/remove subcommands match the reference byte-for-byte (check.sh: 14/14 pass). Unfinished: the import subcommand rejects one exotic flag combination the reference accepts."}}',
    '{"name": "done_tool", "args": {"reasoning": "Answered from the file already in context.", "result": "The timeout is set in config/server.yaml:12 (30s)."}}',
]


@TOOL.register_module(force=True)
class DoneTool(Tool):
    """A tool for indicating that the task has been completed."""

    name: str = "done_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    
    def __init__(self, enable_evolving: bool = False, **kwargs):
        """A tool for indicating that the task has been completed."""
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, 
                       reasoning: str,
                       result: str,
                       **kwargs) -> Response:
        """
        Indicate that the task has been completed.

        Args:
            reasoning (str): A brief why — how you know the task is complete (or why you
                are stopping). Must be provided.
            result (str): The deliverable handoff — what was produced and where, what was
                verified and how, and what remains. For a sub-agent this is all the caller
                sees of your work, so make it self-contained. Must be provided.
        """
        # Convert to string in case LLM returns non-string types
        if reasoning is None or reasoning == "":
            reasoning = "No reasoning provided"
        else:
            reasoning = str(reasoning)
        if result is None or result == "":
            result = "No result provided"
        else:
            result = str(result)
        return Response(type=ResponseType.TOOL, success=True, message=result, data={"reasoning": reasoning, "result": result})
