"""ReadFileTool — read file contents with optional line range."""

import os
from typing import Dict, Any, Optional
from pydantic import Field

from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

_READ_FILE_TOOL_DESCRIPTION = """Read the contents of a file. Returns the file content with line numbers prefixed.

Args:
- path (str): Absolute path to the file to read.
- offset (int, optional): Line number to start reading from (1-based). Defaults to 1.
- limit (int, optional): Maximum number of lines to read. Defaults to 2000.

Example: {"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py"}}
Example: {"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py", "offset": 50, "limit": 100}}
"""


@TOOL.register_module(force=True)
class ReadFileTool(Tool):
    """Read file contents with optional line range, returning numbered lines."""

    name: str = "read_file_tool"
    description: str = _READ_FILE_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        path: str,
        offset: int = 1,
        limit: int = 2000,
        **kwargs,
    ) -> ToolResponse:
        """Read file contents with line numbers.

        Args:
            path: Absolute path to the file.
            offset: First line to return (1-based).
            limit: Maximum number of lines to return.
        """
        try:
            if not os.path.exists(path):
                return ToolResponse(success=False, message=f"Error: File not found: {path}")
            if not os.path.isfile(path):
                return ToolResponse(success=False, message=f"Error: Path is not a file: {path}")

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            start = max(0, offset - 1)
            end = min(start + limit, total_lines)
            selected = all_lines[start:end]

            numbered = "".join(
                f"{start + i + 1}\t{line}" for i, line in enumerate(selected)
            )

            truncation_note = ""
            if end < total_lines:
                truncation_note = (
                    f"\n[File truncated. Showing lines {start+1}–{end} of {total_lines}. "
                    f"Use offset/limit to read more.]"
                )

            return ToolResponse(
                success=True,
                message=numbered + truncation_note,
                extra=ToolExtra(
                    file_path=path,
                    data={"total_lines": total_lines, "start": start + 1, "end": end},
                ),
            )

        except Exception as e:
            return ToolResponse(success=False, message=f"Error reading file: {e}")
