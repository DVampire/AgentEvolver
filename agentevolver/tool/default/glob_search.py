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

_INSTRUCTION = """
## Function
Find files matching a glob pattern within a directory tree.

## Guidance
- The pattern is matched against both the full relative path from root and the bare filename.
- Common noise directories (.git, node_modules, __pycache__, .venv, etc.) are skipped automatically.
- Results are sorted and capped at max_results; refine the pattern if results are truncated.

## Parameters
- pattern (str): Glob pattern, e.g. "*.py" or "src/**/*.ts".
- root (str): Absolute path to the directory to search in.
- max_results (int, optional): Maximum number of results to return. Defaults to 100.

## Example
{"name": "glob_search_tool", "args": {"pattern": "*.py", "root": "/abs/path/to/project"}}
"""


@TOOL.register_module(force=True)
class GlobSearchTool(Tool):
    """Find files matching a glob pattern, skipping common noise directories."""

    name: str = "glob_search_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={"canvas_category": "files"})
    enable_evolving: bool = Field(default=False)

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def _glob_in_sandbox(self, sandbox, pattern: str, root: str, max_results: int) -> Response:
        """Run the same match inside a peer container via find.

        ``find`` is asked for every path and the glob is applied here with ``fnmatch``,
        so matching stays identical to the local branch — including its habit of
        testing the pattern against both the relative path and the bare filename,
        which ``find -name`` alone would not reproduce.
        """
        import shlex

        prunes = " -o ".join(f"-name {shlex.quote(d)}" for d in sorted(_IGNORED_DIRS))
        command = (
            f"find {shlex.quote(root)} \\( {prunes} \\) -prune -o -type f -print 2>/dev/null | sort"
        )
        res = await sandbox.run_command(command)
        if res.exit_code is None:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Error listing {root} in sandbox: {res.as_message()}")

        matches: List[str] = []
        for full in (res.stdout or "").splitlines():
            full = full.strip()
            if not full:
                continue
            rel = os.path.relpath(full, root)
            if fnmatch(rel, pattern) or fnmatch(os.path.basename(full), pattern):
                matches.append(full)
                if len(matches) >= max_results:
                    break

        truncated = len(matches) >= max_results
        if not matches:
            message = f"No files found matching '{pattern}' under {root}"
        else:
            lines = "\n".join(matches)
            suffix = f"\n[Results capped at {max_results}. Refine your pattern to see more.]" if truncated else ""
            message = f"Found {len(matches)} file(s):\n{lines}{suffix}"
        return Response(
            type=ResponseType.TOOL, success=True, message=message,
            data={"matches": matches, "truncated": truncated, "pattern": pattern, "sandboxed": True},
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
            # A peer sandbox bound on the context means the tree worth listing lives in
            # that container (e.g. a programbench task cleanroom). Walking the local
            # filesystem instead would list the base container, where the task's
            # /workspace does not exist — "no files found" would then be a statement
            # about the wrong machine.
            sandbox = (getattr(kwargs.get("ctx"), "extra", None) or {}).get("sandbox")

            if sandbox is None:
                sandbox_denial = check_session_path(kwargs.get("ctx"), root, write=False)
                if sandbox_denial:
                    return Response(type=ResponseType.TOOL, success=False, message=sandbox_denial)

            if sandbox is not None:
                return await self._glob_in_sandbox(sandbox, pattern, root, max_results)

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
