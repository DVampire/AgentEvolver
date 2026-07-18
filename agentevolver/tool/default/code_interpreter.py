"""Code interpreter tool — executes code inside an OpenSandbox container.

Execution now happens in an isolated, Docker-backed
sandbox via ``agentevolver.sandbox`` (the ``code_interpreter`` backend), so it is safe
and supports Python/Bash/JavaScript/TypeScript/Go/Java/R.

The sandbox is reused per session (``ctx.id``), giving REPL-like state
persistence across successive calls within the same session — except for R,
which runs as a fresh ``Rscript`` process on every call (no persisted
variables across calls; see ``agentevolver/sandbox/default/code_interpreter.py``).
"""

from typing import Any, Dict

from pydantic import Field

from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.sandbox import sandbox_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL

_DESCRIPTION = "Execute code in an isolated sandbox and return its output."

_INSTRUCTION = """
## Function
Execute code in an isolated sandbox and return its output.

## Guidance
Runs in a Docker-backed OpenSandbox container with a persistent kernel, so
variables/state carry over across calls in the same session.

## Parameters
- code (str): The code to execute.
- language (str, optional): One of python (default), bash, javascript, typescript, go, java, r.
  Note: r runs as a fresh process each call — variables do NOT persist across calls
  the way they do for the other languages.

## Example
{"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}
{"name": "code_interpreter_tool", "args": {"code": "cat(R.version.string)", "language": "r"}}
"""


@TOOL.register_module(force=True)
class CodeInterpreterTool(Tool):
    """Execute code in a sandboxed, multi-language interpreter."""

    name: str = "code_interpreter_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="danger_full_access", description="Code execution runs in an isolated sandbox.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, code: str, language: str = "python", **kwargs) -> Response:
        """Execute ``code`` in the session's sandbox."""
        ctx = kwargs.get("ctx")
        reuse_key = getattr(ctx, "id", None) or "default"
        try:
            sandbox = await sandbox_manager.acquire("code_interpreter", reuse_key=reuse_key)
            result = await sandbox.run_code(code, language=language)
            logger.info(f"| {'✅' if result.success else '⚠️'} code_interpreter ran {language} code")
            return Response(
                type=ResponseType.TOOL,
                success=result.success,
                message=result.as_message(),
                data={"exit_code": result.exit_code, "results": result.results},
            )
        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error: {e}")
