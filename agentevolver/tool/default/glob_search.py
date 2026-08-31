"""GlobSearchTool — find files matching a glob pattern."""

import os
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.registry import TOOL
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType

_IGNORED_DIRS = frozenset({
    ".git", "node_modules", "target", "dist", "build",
    "coverage", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache",
})

_DESCRIPTION = "Find files matching a glob pattern within a directory tree."

_GUIDANCE = """
- The pattern is matched against both the full relative path from root and the bare filename.
- Common noise directories (.git, node_modules, __pycache__, .venv, etc.) are skipped automatically.
- Results are sorted and capped at max_results; refine the pattern if results are truncated.
"""

_EXAMPLES = [
    '{"name": "glob_search_tool", "args": {"pattern": "*.py", "root": "/abs/path/to/project"}}',
]


@TOOL.register_module(force=True)
class GlobSearchTool(Tool):
    """Find files matching a glob pattern, skipping common noise directories."""

    name: str = "glob_search_tool"
    # Declared, not inherited. The base default is `workspace_write`, which for a
    # tool that only reads is a claim it never had to make — and the two fields
    # plan mode reads are exactly `mutates` and this one, so leaving the wider
    # value in place describes the tool as something it is not.
    permission_mode: str = "read_only"
    #: a tree walk over a large repository, not an unbounded scan.
    call_timeout_seconds: float = 120
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
                op=Operation.READ, target=str(arguments.get("root") or "")
            )

    async def __call__(
        self,
        pattern: str,
        root: str,
        max_results: int = 100,
        **kwargs,
    ) -> Response:
        """Find files matching pattern under root.

        Args:
            pattern:     Glob pattern (matched against the full relative path from root).
            root:        Absolute directory to search.
            max_results: Cap on returned results.
        """
        try:
            denial = check_session_path(kwargs.get("ctx"), root, write=False)
            if denial:
                return Response(type=ResponseType.TOOL, success=False, message=denial)

            if not os.path.isdir(root):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: Not a directory: {root}")

            matches: List[str] = []
            for dirpath, dirnames, filenames in os.walk(root):
                # Prune ignored directories in-place
                dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]

                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, root)
                    if fnmatch(rel, pattern) or fnmatch(filename, pattern):
                        matches.append(full)
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break

            matches.sort()
            truncated = len(matches) >= max_results

            if not matches:
                message = f"No files found matching '{pattern}' under {root}"
            else:
                lines = "\n".join(matches)
                suffix = f"\n[Results capped at {max_results}. Refine your pattern to see more.]" if truncated else ""
                message = f"Found {len(matches)} file(s):\n{lines}{suffix}"

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=message,
                data={"matches": matches, "truncated": truncated, "pattern": pattern},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error in glob search: {e}")
