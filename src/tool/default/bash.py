"""Bash tool for executing shell commands."""
import asyncio
import os
import signal
from typing import Any, Dict, Optional

from pydantic import Field

from src.permission import Operation, PermissionRequest, permission_manager
from src.registry import TOOL
from src.tool.types import Tool, ToolExtra, ToolResponse

MAX_OUTPUT_BYTES = 16384  # match claw-code

_BASH_TOOL_DESCRIPTION = """Execute bash commands in the shell.

IMPORTANT:
- Use this tool to run system commands, scripts, or any bash operations.
- Be careful with commands that modify the system or require elevated privileges.
- For file operations, ALWAYS use ABSOLUTE paths to avoid path-related issues.
- Input should be a VALID bash command string.

Args:
- command (str): The command to execute. If file path is necessary, it should be an absolute path.

Example: {"name": "bash_tool", "args": {"command": "ls -l /path/to/file.txt"}}.
"""


@TOOL.register_module(force=True)
class BashTool(Tool):
    """A tool for executing bash commands asynchronously."""

    name: str = "bash_tool"
    description: str = _BASH_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")
    timeout: int = Field(default=600, description="Timeout in seconds for command execution")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, command: str, work_dir: Optional[str] = None, **kwargs) -> ToolResponse:
        """Execute a bash command asynchronously.

        Args:
            command:   The shell command to run.
            work_dir:  Working directory — used for workspace-boundary checks.
        """
        if not command.strip():
            return ToolResponse(success=False, message="Error: Empty command provided")

        # Permission check
        req = PermissionRequest(op=Operation.BASH, target=command)
        result = permission_manager.check(self.name, req)
        if not result.allowed:
            return ToolResponse(success=False, message=f"Permission denied: {result.reason}")

        warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                return ToolResponse(
                    success=False,
                    message=f"Error: Command timed out after {self.timeout} seconds",
                )

            stdout_str = _truncate(stdout_bytes.decode("utf-8", errors="replace").strip())
            stderr_str = _truncate(stderr_bytes.decode("utf-8", errors="replace").strip())

            parts = []
            if stdout_str:
                parts.append(f"STDOUT:\n{stdout_str}")
            if stderr_str:
                parts.append(f"STDERR:\n{stderr_str}")

            exit_code = process.returncode
            if exit_code != 0:
                parts.append(f"Exit code: {exit_code}")

            message = warning_prefix + ("\n\n".join(parts) if parts else f"Command completed with exit code: {exit_code}")

            return ToolResponse(
                success=exit_code == 0,
                message=message,
                extra=ToolExtra(data={"exit_code": exit_code, "command": command}),
            )

        except Exception as e:
            return ToolResponse(success=False, message=f"Error executing command: {e}")


def _truncate(text: str) -> str:
    """Truncate output to MAX_OUTPUT_BYTES, appending a notice if clipped."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return text
    clipped = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return clipped + f"\n... [output truncated at {MAX_OUTPUT_BYTES} bytes]"
