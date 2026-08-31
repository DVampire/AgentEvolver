"""Read project-owned instructions and durable memory into the fixed context layer."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import List, Optional

from agentevolver.paths import path_manager


PROJECT_CONTEXT_FILES = (
    "AGENTS.override.md", "AGENTS.md", "CLAUDE.md", "MEMORY.md",
)
MAX_PROJECT_CONTEXT_CHARS = 32_000


def _read_direct_file(root: Path, filename: str, limit: int) -> Optional[str]:
    """Read one regular direct child without following a swapped-in symlink."""
    path = path_manager.entry_under(root, filename)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(
            descriptor, "r", encoding="utf-8", errors="replace", closefd=False,
        ) as handle:
            return handle.read(max(0, limit) + 1).strip()
    except OSError:
        return None
    finally:
        os.close(descriptor)


def load_project_context(workspace_root: str) -> str:
    """Load only root-scoped project files; never import host/user instructions."""
    if not workspace_root:
        return ""
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        return ""
    blocks: List[str] = []
    # Override replaces the ordinary AGENTS file at the same scope.
    override = _read_direct_file(root, PROJECT_CONTEXT_FILES[0], MAX_PROJECT_CONTEXT_CHARS)
    selected = (
        [(PROJECT_CONTEXT_FILES[0], override)] if override is not None
        else [(PROJECT_CONTEXT_FILES[1], None)]
    )
    selected.extend((filename, None) for filename in PROJECT_CONTEXT_FILES[2:])
    for filename, prefetched in selected:
        remaining = MAX_PROJECT_CONTEXT_CHARS - len("\n\n".join(blocks))
        if remaining <= 0:
            break
        text = prefetched if prefetched is not None else _read_direct_file(
            root, filename, remaining,
        )
        if text:
            blocks.append(f"### {filename}\n{text}")
    combined = "\n\n".join(blocks)
    if len(combined) > MAX_PROJECT_CONTEXT_CHARS:
        combined = (
            combined[:MAX_PROJECT_CONTEXT_CHARS]
            + f"\n\n[project context clipped after {MAX_PROJECT_CONTEXT_CHARS:,} characters]"
        )
    return combined


__all__ = ["load_project_context"]
