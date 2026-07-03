"""ReadFileTool — read file contents with optional line range."""

import os
from typing import Any, Dict, Optional

from pydantic import Field

from src.permission import Operation, PermissionRequest, permission_manager
from src.registry import TOOL
from src.tool.types import Tool
from src.response.types import Response, ResponseType

_DESCRIPTION = "Read the contents of a file."

_INSTRUCTION = """
## Function
Read the contents of a file.

## Guidance
- Returns the file content with line numbers prefixed.
- By default the whole file is read; use offset/limit to read a specific line range.

## Parameters
- path (str): Absolute path to the file to read.
- offset (int, optional): Line number to start reading from (1-based). Defaults to 1.
- limit (int, optional): Maximum number of lines to read. Defaults to the whole file.

## Example
{"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py"}}
{"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py", "offset": 50, "limit": 100}}
"""


@TOOL.register_module(force=True)
class ReadFileTool(Tool):
    """Read file contents with optional line range, returning numbered lines."""

    name: str = "read_file_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        path: str,
        offset: int = 1,
        limit: Optional[int] = None,
        **kwargs,
    ) -> Response:
        """Read file contents with line numbers.

        Args:
            path:   Absolute path to the file.
            offset: First line to return (1-based).
            limit:  Maximum number of lines to return. None reads to end of file.
        """
        try:
            if not os.path.exists(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: File not found: {path}")
            if not os.path.isfile(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: Path is not a file: {path}")

            # Permission + guard check (size, binary)
            result = permission_manager.check(
                self.name,
                PermissionRequest(op=Operation.READ, target=path),
            )
            if not result.allowed:
                return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            start = max(0, offset - 1)
            end = total_lines if limit is None else min(start + limit, total_lines)
            selected = all_lines[start:end]

            numbered = "".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))

            # Only note when the caller explicitly limited the range (not a default full read).
            truncation_note = ""
            if limit is not None and end < total_lines:
                truncation_note = (
                    f"\n[Showing lines {start+1}–{end} of {total_lines}. "
                    f"Use offset/limit to read more.]"
                )

            warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=warning_prefix + numbered + truncation_note,
                file_path=[path],
                data={"total_lines": total_lines, "start": start + 1, "end": end},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error reading file: {e}")
