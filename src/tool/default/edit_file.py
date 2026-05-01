"""EditFileTool — precise string-replace editing (Claude Code style)."""

import os
from typing import Dict, Any
from pydantic import Field

from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

_EDIT_FILE_TOOL_DESCRIPTION = """Edit a file by replacing an exact string with a new string.

The old_string must appear EXACTLY ONCE in the file. If it appears zero or more than once, the edit is rejected — add more surrounding context to make it unique.

Use read_file_tool first to see the current file content before editing.

Args:
- path (str): Absolute path to the file to edit.
- old_string (str): The exact text to find and replace. Must be unique in the file.
- new_string (str): The text to replace it with. Use empty string "" to delete old_string.

Example: {"name": "edit_file_tool", "args": {"path": "/abs/path/to/file.py", "old_string": "def foo():\\n    pass", "new_string": "def foo():\\n    return 42"}}
"""


@TOOL.register_module(force=True)
class EditFileTool(Tool):
    """Precise string-replace file editor. Requires old_string to be unique in the file."""

    name: str = "edit_file_tool"
    description: str = _EDIT_FILE_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        path: str,
        old_string: str,
        new_string: str,
        **kwargs,
    ) -> ToolResponse:
        """Replace old_string with new_string in the file at path.

        Args:
            path: Absolute path to the file.
            old_string: Exact text to replace (must appear exactly once).
            new_string: Replacement text.
        """
        try:
            if not os.path.exists(path):
                return ToolResponse(success=False, message=f"Error: File not found: {path}")
            if not os.path.isfile(path):
                return ToolResponse(success=False, message=f"Error: Path is not a file: {path}")

            with open(path, "r", encoding="utf-8") as f:
                original = f.read()

            count = original.count(old_string)
            if count == 0:
                return ToolResponse(
                    success=False,
                    message=(
                        "Error: old_string not found in file. "
                        "Use read_file_tool to verify the exact content before editing."
                    ),
                )
            if count > 1:
                return ToolResponse(
                    success=False,
                    message=(
                        f"Error: old_string appears {count} times in the file. "
                        "Provide more surrounding context to make it unique."
                    ),
                )

            updated = original.replace(old_string, new_string, 1)

            with open(path, "w", encoding="utf-8") as f:
                f.write(updated)

            old_lines = old_string.count("\n") + 1
            new_lines = new_string.count("\n") + 1 if new_string else 0
            delta = new_lines - old_lines
            delta_str = f"+{delta}" if delta >= 0 else str(delta)

            return ToolResponse(
                success=True,
                message=f"Edited {path} ({delta_str} lines)",
                extra=ToolExtra(
                    file_path=path,
                    data={"old_lines": old_lines, "new_lines": new_lines, "delta": delta},
                ),
            )

        except Exception as e:
            return ToolResponse(success=False, message=f"Error editing file: {e}")
