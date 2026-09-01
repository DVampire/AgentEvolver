"""Read project-owned instructions and durable memory into the fixed context layer."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from agentevolver.paths import path_manager

PROJECT_CONTEXT_FILES = (
    "AGENTS.override.md", "AGENTS.md", "CLAUDE.md", "MEMORY.md",
)
MAX_PROJECT_CONTEXT_CHARS = 32_000


@dataclass(frozen=True)
class _ContextBlock:
    """One instruction source in the order the model must read it."""

    directory: Path
    filename: str
    text: str
    order: int


def _read_direct_file(root: Path, filename: str, limit: int) -> Optional[str]:
    """Read one regular direct child without following a swapped-in symlink.

    ``limit`` remains in the signature for compatibility with callers, but source text
    is read whole. Aggregate selection below includes or references a source as one unit;
    it never returns a prefix that looks like the complete instruction file.
    """
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
    """Return root-to-leaf instruction scopes for files involved in this task.

    Resolving first is intentional: a task attachment reached through a symlink outside
    the workspace must not make that external directory an instruction authority.
    """
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
) -> str:
    """Load root memory plus root/path-scoped project instructions.

    Root ``MEMORY.md`` and ``CLAUDE.md`` remain project-wide.  ``AGENTS.md`` (or its
    same-directory override) is then layered from the repository root down to every
    active task path, matching the closest-scope-wins convention without importing
    host/user instructions.
    """
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

    # Durable project knowledge is deliberately separate from path rules.
    append(root, "CLAUDE.md")
    append(root, "MEMORY.md")
    try:
        from agentevolver.memory.project import ProjectMemoryStore

        automatic = ProjectMemoryStore(
            str(root), source_workspace=source_workspace,
        ).render()
        if automatic:
            append(root, "AUTO_MEMORY.md", automatic)
    except Exception:
        # Project-owned instructions remain available if optional auto-memory is corrupt.
        pass
    for directory in _scoped_directories(root, active_paths):
        override = _read_direct_file(
            directory, "AGENTS.override.md", MAX_PROJECT_CONTEXT_CHARS,
        )
        if override is not None:
            append(directory, "AGENTS.override.md", override)
        else:
            append(directory, "AGENTS.md")
    def render(block: _ContextBlock, text: Optional[str] = None) -> str:
        scope = (
            "." if block.directory == root
            else block.directory.relative_to(root).as_posix()
        )
        return f"### {block.filename} (scope: {scope})\n{block.text if text is None else text}"

    # The model resolves project rules closest-scope-last, so capacity pressure must
    # preserve that same authority order.  Building from the tail keeps the deepest
    # active-path rules first; the old prefix slice did the opposite and could retain
    # generic root memory while silently deleting the only rules governing the file
    # being edited.  Selected blocks are put back into root-to-leaf order afterwards.
    selected: List[tuple[int, str]] = []
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
        # Continue: a later, lower-priority block can still fit even if this one cannot.

    return separator.join(text for _, text in sorted(selected))


__all__ = ["load_project_context"]
