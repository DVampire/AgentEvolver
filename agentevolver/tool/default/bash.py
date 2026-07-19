"""Bash tool for executing shell commands."""
import asyncio
import os
import signal
import sys
from typing import Any, Dict

from pydantic import Field

from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.sandbox.types import get_current_sandbox
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType

_DESCRIPTION = "Execute bash commands in the shell."

_INSTRUCTION = """
## Function
Execute bash commands in the shell.

## Guidance
- Use this tool to run system commands, scripts, or any bash operations.
- Be careful with commands that modify the system or require elevated privileges.
- For file operations, ALWAYS use ABSOLUTE paths to avoid path-related issues.
- Input should be a VALID bash command string.

## Parameters
- command (str): The command to execute. If file path is necessary, it should be an absolute path.

## Example
{"name": "bash_tool", "args": {"command": "ls -l /path/to/file.txt"}}
"""


@TOOL.register_module(force=True)
class BashTool(Tool):
    """A tool for executing bash commands asynchronously."""

    name: str = "bash_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    timeout: int = Field(default=600, description="Timeout in seconds for command execution")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, command: str, **kwargs) -> Response:
        """Execute a bash command asynchronously.

        Args:
            command:   The shell command to run.
            workspace_root:  Working directory — used for workspace-boundary checks.
        """
        if not command.strip():
            return Response(type=ResponseType.TOOL, success=False, message="Error: Empty command provided")

        ctx = kwargs.get("ctx")
        if getattr(ctx, "extra", {}).get("gateway_session") and get_current_sandbox() is None:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=(
                    "Sandbox blocked host Bash execution. Configure an isolated container backend "
                    "before running shell commands in a Gateway session."
                ),
            )

        # Permission check
        req = PermissionRequest(op=Operation.BASH, target=command)
        result = permission_manager.check(self.name, req)
        if not result.allowed:
            return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

        warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

        try:
            # Keep commands such as ``python3`` and ``pip`` in the same runtime
            # environment that launched AgentEvolver (for example conda's agentos).
            runtime_bin = os.path.dirname(sys.executable)
            command_env = {
                **os.environ,
                "PATH": runtime_bin + os.pathsep + os.environ.get("PATH", ""),
            }
            # Every session command runs from its isolated workspace.  Besides
            # keeping relative outputs contained, this makes ordinary scripts
            # (``open('results/x.json', 'w')``) behave consistently with the
            # workspace path shown to the agent.
            workspace_root = getattr(ctx, "workspace_root", None)
            cwd = os.path.abspath(workspace_root) if workspace_root else None
            if cwd and not os.path.isdir(cwd):
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=f"Workspace directory does not exist: {cwd}",
                )
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=command_env,
                cwd=cwd,
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
                return Response(type=ResponseType.TOOL, 
                    success=False,
                    message=f"Error: Command timed out after {self.timeout} seconds",
                )

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

            parts = []
            if stdout_str:
                parts.append(f"STDOUT:\n{stdout_str}")
            if stderr_str:
                parts.append(f"STDERR:\n{stderr_str}")

            exit_code = process.returncode
            if exit_code != 0:
                parts.append(f"Exit code: {exit_code}")

            message = warning_prefix + ("\n\n".join(parts) if parts else f"Command completed with exit code: {exit_code}")

            return Response(type=ResponseType.TOOL, 
                success=exit_code == 0,
                message=message,
                data={"exit_code": exit_code, "command": command},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error executing command: {e}")
