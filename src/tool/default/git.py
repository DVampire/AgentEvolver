"""GitTool — git operations scoped to a workdir."""

import asyncio
import os
from typing import Dict, Any, Optional
from pydantic import Field

from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

_GIT_TOOL_DESCRIPTION = """Run git operations inside the project workdir.

Available actions:
- status: Show working tree status.
- diff: Show unstaged changes. Optionally pass a file path.
- diff_staged: Show staged (cached) changes.
- log: Show recent commit history. Optionally pass count (default 10).
- add: Stage files. Pass path="." to stage all, or a specific file path.
- commit: Create a commit. Requires message parameter.
- checkout: Checkout a branch or restore a file. Pass target (branch name or file path).
- branch: List branches.

Args:
- action (str): One of: status, diff, diff_staged, log, add, commit, checkout, branch.
- path (str, optional): File or directory path for diff/add/checkout actions.
- message (str, optional): Commit message for commit action.
- count (int, optional): Number of log entries to show (default 10).

Example: {"name": "git_tool", "args": {"action": "status"}}
Example: {"name": "git_tool", "args": {"action": "diff", "path": "src/foo.py"}}
Example: {"name": "git_tool", "args": {"action": "add", "path": "."}}
Example: {"name": "git_tool", "args": {"action": "commit", "message": "fix: handle edge case in parser"}}
Example: {"name": "git_tool", "args": {"action": "log", "count": 5}}
"""


@TOOL.register_module(force=True)
class GitTool(Tool):
    """Git operations scoped to the session workdir."""

    name: str = "git_tool"
    description: str = _GIT_TOOL_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)
    timeout: int = Field(default=60)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def _run(self, args: list[str], cwd: str) -> tuple[int, str, str]:
        """Run a git command, return (returncode, stdout, stderr)."""
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            await process.wait()
            return -1, "", f"git command timed out after {self.timeout}s"

        return (
            process.returncode,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )

    def _get_workdir(self, ctx) -> Optional[str]:
        if ctx and hasattr(ctx, "work_dir") and ctx.work_dir:
            return ctx.work_dir
        return None

    async def __call__(
        self,
        action: str,
        path: Optional[str] = None,
        message: Optional[str] = None,
        count: int = 10,
        **kwargs,
    ) -> ToolResponse:
        """Execute a git operation.

        Args:
            action: Git action to perform.
            path: Optional file/directory path.
            message: Commit message (for commit action).
            count: Number of log entries (for log action).
        """
        ctx = kwargs.get("ctx")
        work_dir = self._get_workdir(ctx)
        if not work_dir:
            return ToolResponse(success=False, message="Error: No work_dir set in context.")
        if not os.path.isdir(work_dir):
            return ToolResponse(success=False, message=f"Error: work_dir not found: {work_dir}")

        try:
            if action == "status":
                rc, out, err = await self._run(["status"], work_dir)
                return self._respond(rc, out, err, "status")

            elif action == "diff":
                git_args = ["diff"]
                if path:
                    git_args.append(path)
                rc, out, err = await self._run(git_args, work_dir)
                return self._respond(rc, out or "(no unstaged changes)", err, "diff")

            elif action == "diff_staged":
                git_args = ["diff", "--cached"]
                if path:
                    git_args.append(path)
                rc, out, err = await self._run(git_args, work_dir)
                return self._respond(rc, out or "(no staged changes)", err, "diff --cached")

            elif action == "log":
                rc, out, err = await self._run(
                    ["log", f"--max-count={count}", "--oneline", "--decorate"], work_dir
                )
                return self._respond(rc, out or "(no commits)", err, "log")

            elif action == "add":
                target = path or "."
                rc, out, err = await self._run(["add", target], work_dir)
                msg = f"Staged: {target}" if rc == 0 else err
                return self._respond(rc, msg, err, "add")

            elif action == "commit":
                if not message:
                    return ToolResponse(success=False, message="Error: commit requires a message parameter.")
                rc, out, err = await self._run(["commit", "-m", message], work_dir)
                return self._respond(rc, out or err, err, "commit")

            elif action == "checkout":
                if not path:
                    return ToolResponse(success=False, message="Error: checkout requires a target (branch or file path).")
                rc, out, err = await self._run(["checkout", path], work_dir)
                return self._respond(rc, out or err or f"Checked out: {path}", err, "checkout")

            elif action == "branch":
                rc, out, err = await self._run(["branch", "-a"], work_dir)
                return self._respond(rc, out or "(no branches)", err, "branch")

            else:
                return ToolResponse(
                    success=False,
                    message=f"Unknown action: {action}. Available: status, diff, diff_staged, log, add, commit, checkout, branch",
                )

        except Exception as e:
            return ToolResponse(success=False, message=f"Error running git {action}: {e}")

    @staticmethod
    def _respond(rc: int, stdout: str, stderr: str, action: str) -> ToolResponse:
        success = rc == 0
        if success:
            return ToolResponse(success=True, message=stdout, extra=ToolExtra(data={"action": action}))
        else:
            msg = stderr or stdout or f"git {action} failed (exit {rc})"
            return ToolResponse(success=False, message=f"Error: {msg}")
