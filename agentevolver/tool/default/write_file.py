"""WriteFileTool — create or overwrite a file with given content."""

import difflib
import os
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType

_DESCRIPTION = "Write content to a file, creating it (and any missing parent directories) if it does not exist, or overwriting it if it does."

_INSTRUCTION = """
## Function
Write content to a file, creating it (and any missing parent directories) if it does not exist, or overwriting it if it does.

## Guidance
- Use this tool to create new files.
- For modifying existing files, prefer edit_file_tool to make targeted changes.

## Parameters
- path (str): Absolute path to the file to write.
- content (str): The full content to write to the file.

## Example
{"name": "write_file_tool", "args": {"path": "/abs/path/to/new_file.py", "content": "print('hello')"}}
"""


@TOOL.register_module(force=True)
class WriteFileTool(Tool):
    """Create or overwrite a file with the given content."""

    name: str = "write_file_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={})
    enable_evolving: bool = Field(default=False)

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(
        self,
        path: str,
        content: str,
        **kwargs,
    ) -> Response:
        """Write content to a file.

        Args:
            path:    Absolute path to the file.
            content: Full content to write.
        """
        try:
            sandbox_denial = check_session_path(kwargs.get("ctx"), path, write=True)
            if sandbox_denial:
                return Response(type=ResponseType.TOOL, success=False, message=sandbox_denial)
            # Permission + size + workspace boundary check
            result = permission_manager.check(
                self.name,
                PermissionRequest(op=Operation.WRITE, target=path, content=content),
                workspace=getattr(kwargs.get("ctx"), "workspace_root", ""),
            )
            if not result.allowed:
                return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Read original for diff (if exists)
            original_lines: List[str] = []
            existed = os.path.exists(path)
            if existed:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        original_lines = f.readlines()
                except OSError:
                    pass

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            new_lines = content.splitlines(keepends=True)
            patch = list(difflib.unified_diff(
                original_lines, new_lines,
                fromfile=f"a/{os.path.basename(path)}",
                tofile=f"b/{os.path.basename(path)}",
                lineterm="",
            ))

            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            action = "Overwrote" if existed else "Created"
            warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=f"{warning_prefix}{action} {path} ({line_count} lines)",
                file_path=[path],
                data={"existed": existed, "line_count": line_count, "patch": patch},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error writing file: {e}")
