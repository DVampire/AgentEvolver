"""GrepSearchTool — search file contents for a pattern."""

import os
import re
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

_DESCRIPTION = "Search file contents for lines matching a regex or literal string."

_INSTRUCTION = """
## Function
Search file contents for lines matching a regex or literal string.

## Guidance
- Only files whose names match file_pattern are searched; common noise directories (.git, node_modules, __pycache__, .venv, etc.) are skipped automatically.
- An invalid regex pattern returns an error rather than matches.
- Results are capped at max_results; each match reports the file path, line number, and line text.

## Parameters
- pattern (str): Regular expression (or literal string) to search for.
- root (str): Absolute path to the directory to search in.
- file_pattern (str, optional): Glob pattern to filter which files are searched, e.g. "*.py". Defaults to all files.
- case_sensitive (bool, optional): Whether the search is case-sensitive. Defaults to true.
- max_results (int, optional): Maximum number of matching lines to return. Defaults to 100.

## Example
{"name": "grep_search_tool", "args": {"pattern": "def __call__", "root": "/abs/path/to/project", "file_pattern": "*.py"}}
"""


@TOOL.register_module(force=True)
class GrepSearchTool(Tool):
    """Search file contents for regex/literal matches, skipping common noise directories."""

    name: str = "grep_search_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={"canvas_category": "files"})
    enable_evolving: bool = Field(default=False)

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def _search_in_sandbox(
        self, sandbox, pattern: str, root: str, file_pattern: str,
        case_sensitive: bool, max_results: int,
    ) -> Response:
        """Run the same search inside a peer container via grep.

        Uses the container's own grep rather than reading files out one by one: the
        result set is what matters here, and a tree walk over the transport would be
        an order of magnitude slower. ``grep -E`` is close enough to Python's ``re``
        for the literal and character-class patterns this tool is used with; a pattern
        relying on Python-only syntax will simply not match, which grep reports as
        zero results rather than an error.
        """
        import shlex

        flags = "-rEn" if case_sensitive else "-rEni"
        excludes = " ".join(f"--exclude-dir={shlex.quote(d)}" for d in sorted(_IGNORED_DIRS))
        command = (
            f"grep {flags} {excludes} --include={shlex.quote(file_pattern)} "
            f"-- {shlex.quote(pattern)} {shlex.quote(root)} 2>/dev/null | head -n {int(max_results)}"
        )
        res = await sandbox.run_command(command)
        # grep exits 1 on "no matches", which is an answer, not a failure.
        if res.exit_code is None:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Error searching {root} in sandbox: {res.as_message()}")

        results: List[Dict[str, Any]] = []
        for line in (res.stdout or "").splitlines():
            path, _, rest = line.partition(":")
            lineno, _, text = rest.partition(":")
            if not lineno.isdigit():
                continue
            results.append({"file": path, "line": int(lineno), "text": text})

        truncated = len(results) >= max_results
        if not results:
            message = f"No matches found for '{pattern}' under {root}"
        else:
            lines = "\n".join(f"{r['file']}:{r['line']}: {r['text']}" for r in results)
            suffix = f"\n[Results capped at {max_results}.]" if truncated else ""
            message = f"Found {len(results)} match(es):\n{lines}{suffix}"
        return Response(
            type=ResponseType.TOOL, success=True, message=message,
            data={"results": results, "truncated": truncated, "pattern": pattern, "sandboxed": True},
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
            # A peer sandbox bound on the context means the tree worth searching lives
            # in that container (e.g. a programbench task cleanroom). Walking the local
            # filesystem instead would search the base container, where the task's
            # /workspace does not exist — returning "no matches" for code that is
            # plainly there, which reads as a fact about the code rather than about
            # the tool.
            sandbox = (getattr(kwargs.get("ctx"), "extra", None) or {}).get("sandbox")

            # The host-root boundary check only applies to local searches: with a peer
            # bound, the container is the isolation boundary.
            if sandbox is None:
                sandbox_denial = check_session_path(kwargs.get("ctx"), root, write=False)
                if sandbox_denial:
                    return Response(type=ResponseType.TOOL, success=False, message=sandbox_denial)

            if sandbox is not None:
                return await self._search_in_sandbox(
                    sandbox, pattern, root, file_pattern, case_sensitive, max_results,
                )

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
