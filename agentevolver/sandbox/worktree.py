"""Disposable Git worktrees used to isolate concurrently writing agents."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentevolver.hook.events import HookEvent


async def _git(cwd: Path, *args: str, stdin: Optional[bytes] = None) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(cwd), *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(stdin)
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


@dataclass
class IsolatedWorktree:
    source: Path
    #: Effective child cwd. This may be a subdirectory of the Git worktree when the
    #: session workspace itself is a repository subdirectory.
    path: Path
    #: Source repository root, used to register/remove the linked worktree.
    repository: Path
    worktree_root: Path
    baseline: str = "HEAD"

    @classmethod
    async def create(cls, source: str, storage_root: str, token: str) -> "IsolatedWorktree":
        source_path = Path(source).expanduser().resolve()
        code, root, error = await _git(source_path, "rev-parse", "--show-toplevel")
        if code:
            raise RuntimeError(
                f"isolated worktree requires a Git workspace: {error.strip() or source}"
            )
        repository = Path(root.strip()).resolve()
        try:
            relative_workspace = source_path.relative_to(repository)
        except ValueError as error:
            raise RuntimeError(
                f"workspace {source_path} is outside Git repository {repository}"
            ) from error
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", token or "child")[:80]
        parent = Path(storage_root).expanduser().resolve() / "agent-worktrees"
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / safe
        if target.exists():
            raise RuntimeError(f"isolated worktree target already exists: {target}")

        code, _, error = await _git(repository, "worktree", "add", "--detach", str(target), "HEAD")
        if code:
            raise RuntimeError(f"could not create isolated worktree: {error.strip()}")
        execution_path = (target / relative_workspace).resolve()
        instance = cls(
            source=source_path,
            path=execution_path,
            repository=repository,
            worktree_root=target,
        )
        try:
            await instance._seed_working_state()
            code, revision, error = await _git(instance.worktree_root, "rev-parse", "HEAD")
            if code:
                raise RuntimeError(f"could not identify isolated baseline: {error.strip()}")
            instance.baseline = revision.strip()
            await instance._emit_lifecycle(
                HookEvent.WORKTREE_CREATE,
                {"source": str(source_path), "worktree": str(target), "token": token},
            )
            return instance
        except Exception:
            await instance.cleanup()
            raise

    async def _seed_working_state(self) -> None:
        """Make the child start from the parent's tracked and untracked working state."""
        code, patch, error = await _git(
            self.source, "diff", "--binary", "HEAD", "--", ".",
        )
        if code:
            raise RuntimeError(f"could not snapshot parent changes: {error.strip()}")
        if patch:
            code, _, error = await _git(
                self.worktree_root, "apply", "--binary", "-", stdin=patch.encode(),
            )
            if code:
                raise RuntimeError(f"could not seed parent changes: {error.strip()}")

        code, output, error = await _git(
            self.source, "ls-files", "--others", "--exclude-standard", "-z",
        )
        if code:
            raise RuntimeError(f"could not list parent untracked files: {error.strip()}")
        for value in output.split("\0"):
            if not value:
                continue
            relative = Path(value)
            source = (self.source / relative).resolve()
            target = (self.path / relative).resolve()
            if self.source not in source.parents or self.path not in target.parents:
                raise RuntimeError(f"unsafe untracked path while seeding worktree: {value}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            elif source.exists():
                shutil.copy2(source, target)

        await _git(self.path, "add", "-A", "--", ".")
        code, _, _ = await _git(
            self.path, "diff", "--cached", "--quiet", "--", ".",
        )
        if code == 1:
            code, _, error = await _git(
                self.worktree_root,
                "-c", "user.name=AgentEvolver",
                "-c", "user.email=agent@localhost",
                "commit", "-m", "AgentEvolver isolated worker baseline",
            )
            if code:
                raise RuntimeError(f"could not commit isolated baseline: {error.strip()}")
        elif code:
            raise RuntimeError("could not inspect isolated baseline")

    async def collect_patch(self) -> str:
        # Intent-to-add makes untracked text/binary files appear in the unified patch
        # without staging their contents into the shared repository index.
        await _git(self.path, "add", "-N", "--", ".")
        code, output, error = await _git(
            self.path, "diff", "--binary", self.baseline, "--", ".",
        )
        if code:
            raise RuntimeError(f"could not collect child patch: {error.strip()}")
        return output

    async def cleanup(self) -> None:
        if not self.worktree_root.exists():
            return
        await self._emit_lifecycle(
            HookEvent.WORKTREE_REMOVE, {"worktree": str(self.worktree_root)},
        )
        await _git(
            self.repository, "worktree", "remove", "--force", str(self.worktree_root),
        )

    @staticmethod
    async def _emit_lifecycle(event: HookEvent, payload: dict) -> None:
        try:
            from agentevolver.hook import hook_manager

            await hook_manager.emit(event, payload)
        except Exception:
            # Worktree safety and cleanup must not depend on optional observers.
            return


__all__ = ["IsolatedWorktree"]
