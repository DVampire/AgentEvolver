"""Code interpreter tool — executes code inside an OpenSandbox container.

Replaces the old in-process ``python_interpreter_tool`` (which used the
``executor`` package). Execution now happens in an isolated, Docker-backed
sandbox via ``src.sandbox`` (the ``code_interpreter`` backend), so it is safe
and supports Python/Bash/JavaScript/TypeScript/Go/Java.

The sandbox is reused per session (``ctx.id``), giving REPL-like state
persistence across successive calls within the same session.
"""

from typing import Any, Dict

from pydantic import Field

from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.sandbox import sandbox_manager
from src.logger import logger
from src.registry import TOOL

_CODE_INTERPRETER_TOOL_DESCRIPTION = """Execute code in an isolated sandbox and return its output.
Runs in a Docker-backed OpenSandbox container with a persistent kernel, so
variables/state carry over across calls in the same session.

Args:
- code (str): The code to execute.
- language (str, optional): One of python (default), bash, javascript, typescript, go, java.

Example: {"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}.
"""


@TOOL.register_module(force=True)
class CodeInterpreterTool(Tool):
    """Execute code in a sandboxed, multi-language interpreter."""

    name: str = "code_interpreter_tool"
    description: str = _CODE_INTERPRETER_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")
    permission_mode: str = Field(default="danger_full_access", description="Code execution runs in an isolated sandbox.")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

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
