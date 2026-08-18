"""GrepSearchTool — search file contents for a pattern."""

import os
import re
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.permission.types import is_binary_file
from agentevolver.registry import TOOL
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType

_IGNORED_DIRS = frozenset({
    ".git", "node_modules", "target", "dist", "build",
    "coverage", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache",
})

_DESCRIPTION = "Search file contents for lines matching a regex or literal string."

_GUIDANCE = """
- Only files whose names match file_pattern are searched; common noise directories (.git, node_modules, __pycache__, .venv, etc.) are skipped automatically.
- An invalid regex pattern returns an error rather than matches.
- Results are capped at max_results; each match reports the file path, line number, and line text.
"""

_EXAMPLES = [
    '{"name": "grep_search_tool", "args": {"pattern": "def __call__", "root": "/abs/path/to/project", "file_pattern": "*.py"}}',
]


@TOOL.register_module(force=True)
class GrepSearchTool(Tool):
    """Search file contents for regex/literal matches, skipping common noise directories."""

    name: str = "grep_search_tool"
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
    metadata: Dict[str, Any] = Field(default={"canvas_category": "files"})
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
        file_pattern: str = "*",
        case_sensitive: bool = True,
        max_results: int = 100,
        **kwargs,
    ) -> Response:
        """Search file contents for pattern.

        Args:
            pattern:        Regex pattern to search.
            root:           Absolute directory to search.
            file_pattern:   Glob filter for filenames.
            case_sensitive: Case-sensitive matching.
            max_results:    Cap on returned matches.
        """
        try:
            denial = check_session_path(kwargs.get("ctx"), root, write=False)
            if denial:
                return Response(type=ResponseType.TOOL, success=False, message=denial)

            if not os.path.isdir(root):
                return Response(type=ResponseType.TOOL, success=False, message=f"Error: Not a directory: {root}")

            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                compiled = re.compile(pattern, flags)
            except re.error as e:
                return Response(type=ResponseType.TOOL, success=False, message=f"Invalid regex pattern: {e}")

            from fnmatch import fnmatch

            results: List[Dict[str, Any]] = []

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]

                for filename in filenames:
                    if not fnmatch(filename, file_pattern):
                        continue

                    full = os.path.join(dirpath, filename)
                    # A compiled binary can "match" any pattern by coincidence and the
                    # hit is a line of mojibake. Observed searching a workspace holding a
                    # reference executable: a search for "Usage" returned three matches,
                    # one of them a screenful of bytes out of the binary. That is worse
                    # than no result — it is a plausible-looking answer the agent has to
                    # spend a turn discarding.
                    if is_binary_file(full):
                        continue
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as f:
                            for lineno, line in enumerate(f, 1):
                                if compiled.search(line):
                                    results.append({
                                        "file": full,
                                        "line": lineno,
                                        "text": line.rstrip("\n"),
                                    })
                                    if len(results) >= max_results:
                                        break
                    except OSError:
                        continue

                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break

            truncated = len(results) >= max_results

            if not results:
                message = f"No matches found for '{pattern}' under {root}"
            else:
                lines = "\n".join(
                    f"{r['file']}:{r['line']}: {r['text']}" for r in results
                )
                suffix = f"\n[Results capped at {max_results}.]" if truncated else ""
                message = f"Found {len(results)} match(es):\n{lines}{suffix}"

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=message,
                data={"results": results, "truncated": truncated, "pattern": pattern},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error in grep search: {e}")
