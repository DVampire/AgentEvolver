"""Apply one validated unified diff to the active workspace."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.paths import path_manager
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.sandbox.project import check_session_path
from agentevolver.session import isolated_workspace_root, resolve_workspace_root
from agentevolver.tool.types import Tool

_DESCRIPTION = (
    "Apply one single-file unified diff in the active workspace. Every hunk header must "
    "include ranges such as `@@ -1,2 +1,3 @@`; a bare `@@` is invalid."
)

_GUIDANCE = """
Use this for authored source/configuration changes. Use `bash_tool` to inspect first and to
run syntax checks, formatters, builds, and tests afterwards.

- Send either a Git-style unified diff (`diff --git`, `---`, `+++`) or a classic
  unified diff (`---`, `+++`). Every hunk needs a complete range header such as
  `@@ -1,2 +1,3 @@`; never send a bare `@@`.
- Change exactly one file per call. For a large new file, add a small skeleton first and
  extend it with later patches instead of emitting the whole application at once.
- Context lines must match the current file. A stale or malformed patch is rejected without
  changing the workspace; inspect again and generate a fresh, smaller patch.
- Paths are workspace-relative `a/...` and `b/...` paths. Absolute paths, traversal,
  binary patches, renames, and writes outside the active workspace are rejected.
"""

_EXAMPLES = [
    '{"name":"apply_patch_tool","args":{"patch":"diff --git a/app.js b/app.js\\n--- a/app.js\\n+++ b/app.js\\n@@ -1 +1 @@\\n-console.log(\\\"old\\\");\\n+console.log(\\\"new\\\");\\n"}}',
]


class PatchError(ValueError):
    """The patch is not one safe workspace-local file operation."""


def _header_path(raw: str) -> str:
    """Normalize one Git diff path and reject paths with ambiguous shell quoting."""
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError as error:
        raise PatchError(f"invalid patch path: {error}") from error
    if len(parts) != 1:
        raise PatchError("patch paths containing whitespace are not supported")
    value = parts[0]
    if value == "/dev/null":
        return ""
    if value.startswith(("a/", "b/")):
        value = value[2:]
    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts:
        raise PatchError(f"unsafe patch path: {value!r}")
    return candidate.as_posix()


def patch_target(patch: str) -> str:
    """Return the single logical target named by a standard unified diff."""
    if not isinstance(patch, str) or not patch.strip():
        raise PatchError("patch must be a non-empty unified diff")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise PatchError("binary patches are not supported")

    paths: List[str] = []
    diff_headers = 0
    old_headers = 0
    new_headers = 0
    hunk_headers = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            diff_headers += 1
            try:
                fields = shlex.split(line)
            except ValueError as error:
                raise PatchError(f"invalid diff header: {error}") from error
            if len(fields) != 4:
                raise PatchError("expected `diff --git a/path b/path`")
            paths.extend(filter(None, (_header_path(fields[2]), _header_path(fields[3]))))
        elif line.startswith("--- ") or line.startswith("+++ "):
            if line.startswith("--- "):
                old_headers += 1
            else:
                new_headers += 1
            # A timestamp may follow a classic unified-diff path after a tab. Git-style
            # patches emitted by models normally have none, but accepting it costs no
            # ambiguity because the path ends at the first tab.
            raw = line[4:].split("\t", 1)[0]
            paths.extend(filter(None, (_header_path(raw),)))
        elif line.startswith(("rename from ", "rename to ")):
            raise PatchError("renames are not supported; add and delete in separate calls")
        elif line.startswith("@@"):
            if not re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$", line):
                raise PatchError(
                    "invalid hunk header; use a complete range such as "
                    "`@@ -1,2 +1,3 @@`, not bare `@@`"
                )
            hunk_headers += 1

    if diff_headers not in (0, 1):
        raise PatchError(
            "patch must contain zero or exactly one `diff --git` file section"
        )
    if old_headers != 1 or new_headers != 1:
        raise PatchError("patch must contain exactly one `---`/`+++` file header pair")
    if hunk_headers == 0:
        raise PatchError("patch must contain at least one complete `@@ -... +... @@` hunk")
    unique = list(dict.fromkeys(paths))
    if len(unique) != 1:
        raise PatchError(f"patch must affect exactly one file, found: {unique or 'none'}")
    return unique[0]


def _workspace_target(patch: str, ctx: Any) -> tuple[Path, Path]:
    workspace_raw = resolve_workspace_root(ctx)
    if not workspace_raw:
        raise PatchError("apply_patch_tool requires an active workspace")
    workspace = Path(workspace_raw).expanduser().resolve()
    if not workspace.is_dir():
        raise PatchError(f"workspace does not exist: {workspace}")
    target = path_manager.resolve_under(workspace, patch_target(patch))
    denial = check_session_path(ctx, str(target), write=True)
    if denial:
        raise PatchError(denial)
    return workspace, target


@TOOL.register_module(force=True)
class ApplyPatchTool(Tool):
    """Apply one atomic, workspace-scoped text patch."""

    name: str = "apply_patch_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={"canvas_category": "files"})
    enable_evolving: bool = False
    permission_mode: str = "workspace_write"
    mutates: bool = True
    call_timeout_seconds: float = 60

    def permission_request(self, arguments, ctx=None):
        patch = str(arguments.get("patch") or "")
        _, target = _workspace_target(patch, ctx)
        return PermissionRequest(
            op=Operation.WRITE, target=str(target), content=patch,
        )

    async def __call__(self, patch: str, **kwargs: Any) -> Response:
        """Validate and apply a standard unified diff.

        Args:
            patch: Complete standard unified diff for exactly one workspace-relative file.
            **kwargs: Runtime-injected values, including the active Agent context.
        """
        ctx = kwargs.get("ctx")
        try:
            workspace, target = _workspace_target(patch, ctx)
            existed_before = target.is_file()
            content_before = target.read_bytes() if existed_before else None
            request = PermissionRequest(
                op=Operation.WRITE, target=str(target), content=patch,
            )
            decision = permission_manager.check_declared(
                self.name,
                request,
                mode=self.permission_mode,
                workspace=isolated_workspace_root(ctx),
            )
            if not decision.allowed:
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=f"Permission denied: {decision.reason}",
                )

            command = [
                "git", "apply", "--recount", "--whitespace=nowarn", "-",
            ]
            checked = subprocess.run(
                [*command[:2], "--check", *command[2:]],
                cwd=workspace,
                input=patch,
                text=True,
                capture_output=True,
                timeout=45,
            )
            if checked.returncode:
                detail = (checked.stderr or checked.stdout or "patch did not apply").strip()
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=f"Patch rejected; workspace unchanged: {detail}",
                )
            applied = subprocess.run(
                command,
                cwd=workspace,
                input=patch,
                text=True,
                capture_output=True,
                timeout=45,
            )
            if applied.returncode:
                detail = (applied.stderr or applied.stdout or "patch did not apply").strip()
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=f"Patch failed after validation: {detail}",
                )
            exists_after = target.is_file()
            content_after = target.read_bytes() if exists_after else None
            if (existed_before, content_before) == (exists_after, content_after):
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=(
                        "Patch command reported success but left the target unchanged; "
                        "workspace unchanged. Check the unified-diff hunk ranges."
                    ),
                )
        except (PatchError, OSError, subprocess.SubprocessError) as error:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"Patch rejected; workspace unchanged: {error}",
            )

        additions = sum(
            1 for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in patch.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        warning = f"Warning: {decision.warning}\n\n" if decision.warning else ""
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=(
                f"{warning}Applied patch to {target} "
                f"(+{additions}/-{deletions} lines). Run the relevant check now."
            ),
            files=[str(target)],
            data={
                "path": str(target),
                "additions": additions,
                "deletions": deletions,
            },
        )


__all__ = ["ApplyPatchTool", "PatchError", "patch_target"]
