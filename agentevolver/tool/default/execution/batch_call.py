"""Code mode: the model writes a program that calls its tools, instead of one call a turn.

This is the transport half of code mode. The program runs in `agentevolver.code`'s child
interpreter; what this file owns is the one thing that makes the arrangement safe — every
call the program makes goes back out through the agent's own action path, the same one a
call over the wire takes.

That is not a detail of the implementation, it is the design. The agent hands this tool a
`GuardedDispatch` bound to the turn that is running; each binding is that callable with a
name attached. So a `write_file_tool` call from inside a program takes the plan-mode gate,
the read-only refusal, the tool's own `permission_manager.check`, and the PRE/POST hook
pair, in that order, exactly as it would have alone. There is no other path: with no
dispatch — the tool called outside an agent — the binding table is empty and every call
in the program raises. A fallback that reached `tool_manager` directly would be a second
way to call every tool with none of the checks, which is the one thing this must not be.

It does not replace `code_interpreter_tool`, which answers a different question. That tool
holds a kernel open so a variable defined in one call is there in the next, and captures
figures; it is for analysis, and its code calls no tools. This one is a transport: a fresh
process every time, no state, no figures, and the tools are the point.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.code import BATCH_CALL_TOOL, GuardedDispatch, code_runtime
from agentevolver.session import resolve_workspace_root
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.default.execution.sdk import callable_names
from agentevolver.tool.types import Tool

_DESCRIPTION = (
    "Make a batch of tool calls in one turn, by writing a short Python program that calls "
    "them. Use it for a loop, a search, or the same edit across a list — not for a single "
    "call."
)

_GUIDANCE = """
Make a batch of tool calls in one turn. You write a short Python program; the calls it
makes are the point, and the code is only how you express the batch.

The code you send is the BODY of an async function: `await` and `return` work at the top
level of it. Call a tool as `await tools.<tool_name>(argument=value)`, keyword arguments
only, using the arguments documented in that tool's own instruction. Each call returns the
tool's output as text; a call that fails raises `ToolCallError`.

Only what you `print()` and what you `return` comes back. That is the reason to use this:
read five files and return the one line that matters, instead of paying for five results
you have to read past.

Every tool called from a program is dispatched exactly as if you had called it yourself —
same permission check, same plan mode, same approval. A program does not get you around
anything, and it will stop on a refusal like any other call.

Use it when the calls form a batch, a loop, or a search: read every file matching a
pattern, grep several directories and count the hits, apply one edit across a list. Do NOT
use it for a single call — call the tool. Do not use it for stateful analysis; that is
`code_interpreter_tool`, which keeps its variables between calls. This one is a fresh
process every time.

Independent reads may overlap with `asyncio.gather`. Calls that change something should
run one at a time, in the order you want them applied.
"""

_EXAMPLES = [
    '{"name": "batch_call_tool", "args": {"code": "found = []\\\\nfor path in [\'a.py\', \'b.py\']:\\\\n    text = await tools.read_file_tool(file_path=path)\\\\n    if \'TODO\' in text:\\\\n        found.append(path)\\\\nprint(found)\\\\nreturn f\'{len(found)} files have TODOs\'", "description": "Find which files carry TODOs"}}',
]


@TOOL.register_module(force=True)
class BatchCallTool(Tool):
    """Execute a model-written program whose tool calls re-enter the guarded dispatch."""

    #: Spelled out rather than taken from ``BATCH_CALL_TOOL``, because the registration
    #: check and the catalog generator both read this line from the source without
    #: importing it — a name behind a constant is a tool they cannot see. The two are
    #: held together by a test instead.
    name: str = "batch_call_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    #: The program is arbitrary code, and the tools it calls are whatever the agent holds.
    #: Anything narrower would be a claim about code nobody has read yet.
    permission_mode: str = Field(default="danger_full_access", description="Runs an arbitrary program.")
    #: Not `False`, and not `True` either: what a program changes depends on what the
    #: model wrote. Plan mode admits an action only on `mutates is False`, so a program
    #: is refused while a plan is waiting for approval — which is right, since the reason
    #: to reach for this tool is a batch of work.
    mutates: Optional[bool] = Field(
        default=None, description="Depends on the program; refused in plan mode for that reason."
    )
    #: Bounds the program. Kept under `call_timeout_seconds` so the tool reports its own
    #: timeout — with the logs the program had already printed — instead of being cut off
    #: by the manager with nothing to show.
    timeout: float = Field(default=600.0, description="Wall-clock budget for one program, in seconds.")
    call_timeout_seconds: Optional[float] = Field(
        default=660.0, description="Budget for one call of this tool, in seconds."
    )
    max_parallel_sub_calls: int = Field(
        default=8, description="How many tool calls from one program may be in flight at once."
    )

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, code: str, description: str = "", **kwargs) -> Response:
        """Run ``code`` as a program, with this turn's tools bound into it.

        Args:
            code: The program — the body of an async function.
            description: What the program does, in a few words, for the UI.
        """
        if not (code or "").strip():
            return Response(type=ResponseType.TOOL, success=False,
                            message="Error: `code` is empty. Send the program to run.")

        ctx = kwargs.get("ctx")
        dispatch: Optional[GuardedDispatch] = kwargs.get("sub_dispatch")
        bindings = self._bindings(dispatch)

        logger.info(f"| 🧩 run_code: {description or 'program'} ({len(bindings)} tools bound)")
        result = await code_runtime.run(
            code,
            bindings,
            timeout=self.timeout,
            workspace=resolve_workspace_root(ctx),
            max_parallel=self.max_parallel_sub_calls,
        )

        message = result.as_message()
        if not bindings:
            # Silence here would be read as "the tools were there and I called them
            # wrong". The program ran; it simply had nothing to call.
            message = (
                "No tools were callable from this program — it ran outside an agent's "
                "dispatch, and there is no unguarded path to them.\n\n" + message
            )
        logger.info(
            f"| {'✅' if result.success else '⚠️'} run_code finished: "
            f"{result.calls} tool call(s), {len(result.logs)} line(s) of output"
        )
        return Response(
            type=ResponseType.TOOL,
            success=result.success,
            message=message,
            data={
                "description": description,
                "tool_calls": result.calls,
                "failure": result.failure.model_dump(mode="json") if result.failure else None,
                "value": result.value,
            },
        )

    @staticmethod
    def _bindings(dispatch: Optional[GuardedDispatch]) -> Dict[str, Any]:
        """One async binding per callable name, each one the agent's dispatch with a name.

        `functools.partial` rather than a closure over the loop variable: a `lambda` in a
        comprehension captures the variable, not its value, and every binding would end up
        calling whichever tool happened to be last.
        """
        if dispatch is None:
            return {}
        return {name: functools.partial(dispatch.call, name)
                for name in callable_names(dispatch.names)}


__all__ = ["BatchCallTool"]
