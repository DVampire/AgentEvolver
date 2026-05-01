"""WriteFileTool — create or overwrite a file with given content."""

import os
from typing import Dict, Any
from pydantic import Field

from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

_WRITE_FILE_TOOL_DESCRIPTION = """Write content to a file, creating it (and any missing parent directories) if it does not exist, or overwriting it if it does.

Use this tool to create new files. For modifying existing files, prefer edit_file_tool to make targeted changes.

Args:
- path (str): Absolute path to the file to write.
- content (str): The full content to write to the file.

Example: {"name": "write_file_tool", "args": {"path": "/abs/path/to/new_file.py", "content": "print('hello')"}}
"""


@TOOL.register_module(force=True)
class WriteFileTool(Tool):
    """Create or overwrite a file with the given content."""

    name: str = "write_file_tool"
    description: str = _WRITE_FILE_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        path: str,
        content: str,
        **kwargs,
    ) -> ToolResponse:
        """Write content to a file.

        Args:
            path: Absolute path to the file.
            content: Full content to write.
        """
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            existed = os.path.exists(path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            action = "Overwrote" if existed else "Created"

            return ToolResponse(
                success=True,
                message=f"{action} {path} ({line_count} lines)",
                extra=ToolExtra(
                    file_path=path,
                    data={"existed": existed, "line_count": line_count},
                ),
            )

        except Exception as e:
            return ToolResponse(success=False, message=f"Error writing file: {e}")
