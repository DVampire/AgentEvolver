"""Project-owned instructions that ride in the fixed layer.

CLAUDE.md, MEMORY.md and the nearest AGENTS.md files are project references. The Agent
places this snapshot in fixed user context, below system instructions; learned notes
are loaded separately under the actor's own identity.

Reads are deliberately paranoid - direct children only, no symlink following, regular
files only. This content is injected into a prompt verbatim, so a swapped-in link is a
prompt-injection vector rather than a file-handling inconvenience.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from agentevolver.paths import path_manager

PROJECT_CONTEXT_FILES = ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md", "MEMORY.md")
MAX_PROJECT_CONTEXT_CHARS = 32_000


@dataclass(frozen=True)
class _ContextBlock:
    directory: Path
    filename: str
    text: str
    order: int


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
            return handle.read().strip()
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _scoped_directories(root: Path, active_paths: Optional[Iterable[str]]) -> List[Path]:
    """Return root-to-leaf instruction scopes for files involved in this task."""
    scopes = {root}
    for value in active_paths or ():
        try:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        directory = resolved if resolved.is_dir() else resolved.parent
        current = root
        for part in relative.parts[:len(directory.relative_to(root).parts)]:
            current = current / part
            scopes.add(current)
    return sorted(scopes, key=lambda path: (len(path.relative_to(root).parts), str(path)))


def load_project_context(
    workspace_root: str,
    active_paths: Optional[Iterable[str]] = None,
    *,
    source_workspace: Optional[str] = None,
    include_memory: bool = True,
) -> str:
    """Load durable memory and root-to-leaf project instructions into fixed context."""
    if not workspace_root:
        return ""
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        return ""
    blocks: List[_ContextBlock] = []

    def append(directory: Path, filename: str, prefetched: Optional[str] = None) -> None:
        text = prefetched if prefetched is not None else _read_direct_file(
            directory, filename, MAX_PROJECT_CONTEXT_CHARS,
        )
        if text:
            blocks.append(_ContextBlock(directory, filename, text, len(blocks)))

    append(root, "CLAUDE.md")
    if include_memory:
        append(root, "MEMORY.md")
    # Learned notes have a separate, actor-scoped reference message. Legacy automatic
    # JSON remains readable through its API but is no longer injected as instructions.
    for directory in _scoped_directories(root, active_paths):
        override = _read_direct_file(
            directory, "AGENTS.override.md", MAX_PROJECT_CONTEXT_CHARS,
        )
        if override is not None:
            append(directory, "AGENTS.override.md", override)
        else:
            append(directory, "AGENTS.md")

    def render(block: _ContextBlock, text: Optional[str] = None) -> str:
        scope = "." if block.directory == root else block.directory.relative_to(root).as_posix()
        return f"### {block.filename} (scope: {scope})\n{block.text if text is None else text}"

    selected: List[Tuple[int, str]] = []
    remaining = MAX_PROJECT_CONTEXT_CHARS
    separator = "\n\n"
    for block in reversed(blocks):
        separator_cost = len(separator) if selected else 0
        available = remaining - separator_cost
        if available <= 0:
            break
        rendered = render(block)
        if len(rendered) <= available:
            selected.append((block.order, rendered))
            remaining -= separator_cost + len(rendered)
            continue
        reference = render(
            block,
            "[Source omitted as one complete unit because it exceeds the fixed-context "
            f"budget; read the exact file at {block.directory / block.filename}]",
        )
        if len(reference) <= available:
            selected.append((block.order, reference))
            remaining -= separator_cost + len(reference)
    return separator.join(text for _, text in sorted(selected))


__all__ = [
    "MAX_PROJECT_CONTEXT_CHARS",
    "PROJECT_CONTEXT_FILES",
    "load_project_context",
]
