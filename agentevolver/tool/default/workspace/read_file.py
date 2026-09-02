"""ReadFileTool — read file contents with optional line range."""

import os
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.config import config
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.session import isolated_workspace_root

_DESCRIPTION = "Read the contents of a file."

_GUIDANCE = """
- Returns the file content with line numbers prefixed.
- By default the whole file is read; use offset/limit to read a specific line range.
"""

_EXAMPLES = [
    '{"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py"}}',
    '{"name": "read_file_tool", "args": {"path": "/abs/path/to/file.py", "offset": 50, "limit": 100}}',
]


@TOOL.register_module(force=True)
class ReadFileTool(Tool):
    """Read file contents with optional line range, returning numbered lines."""

    name: str = "read_file_tool"
    # Declared, not inherited. The base default is `workspace_write`, which for a
    # tool that only reads is a claim it never had to make — and the two fields
    # plan mode reads are exactly `mutates` and this one, so leaving the wider
    # value in place describes the tool as something it is not.
    permission_mode: str = "read_only"
    #: reading a file is local I/O; a minute means the path is on a wedged mount, not that the file is big.
    call_timeout_seconds: float = 60
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(
        default={"canvas_category": "files", "programmatic": True},
    )
    enable_evolving: bool = Field(default=False)
    mutates: bool = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    def permission_request(self, arguments, ctx=None):
        return PermissionRequest(
            op=Operation.READ, target=str(arguments.get("path") or "")
        )

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
            denial = check_session_path(kwargs.get("ctx"), path, write=False)
            if denial:
                return Response(type=ResponseType.TOOL, success=False, message=denial)

            if not os.path.exists(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: File not found: {path}")
            if not os.path.isfile(path):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: Path is not a file: {path}")

            # Permission + guard check (size, binary)
            result = permission_manager.check_declared(
                self.name,
                PermissionRequest(op=Operation.READ, target=path),
                mode=self.permission_mode,
                workspace=isolated_workspace_root(kwargs.get("ctx")),
            )
            if not result.allowed:
                return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")
            warning = result.warning or ""

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

            warning_prefix = f"Warning: {warning}\n\n" if warning else ""

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=warning_prefix + numbered + truncation_note,
                files=[path],
                data={"total_lines": total_lines, "start": start + 1, "end": end},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error reading file: {e}")
