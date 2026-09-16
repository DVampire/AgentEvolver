"""Bounded, read-only project documents for the workbench overview.

Reading the overview never binds runtime state, imports an extension or creates a
memory namespace. These files belong to the requested project's existing roots.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


def read_document(root: Path, relative: str, limit: int = 32_000) -> dict[str, Any]:
    result: dict[str, Any] = {"path": relative, "exists": False, "content": "", "truncated": False}
    path = root / relative
    try:
        # Reject redirects, including directories above the document. Open only a
        # regular file: a FIFO should never block a gateway request.
        if path.resolve() != path.absolute():
            raise ValueError("Document redirects through a symlink")
        path.resolve().relative_to(root.resolve())
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Document is not a regular file")
            content = stream.read(limit + 1)
        result.update(exists=True, content=content[:limit], truncated=len(content) > limit)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, ValueError) as error:
        result["error"] = str(error)
    return result


def project_documents(sandbox: Any) -> dict[str, Any]:
    """A plan and shared session notes, without inferring progress or quality."""
    from agentevolver.paths import P, path_manager

    memory_root = path_manager.under(
        path_manager.under(sandbox.log_root, P.LOG_MODULE, module="memory"),
        P.MEMORY_ACTOR_NOTES, actor_id="shared",
    )
    notes = []
    memory_error = None
    truncated = False
    try:
        if memory_root.resolve() != memory_root.absolute():
            raise ValueError("Memory directory redirects through a symlink")
        if memory_root.is_dir():
            # Shared notes only. Actor-private namespaces are not a project library.
            for path in sorted(memory_root.glob("*.md")):
                if len(notes) == 30:
                    truncated = True
                    break
                notes.append(read_document(memory_root, path.name, limit=4_000))
    except (OSError, ValueError) as error:
        memory_error = str(error)
    return {
        "plan": {
            "index": read_document(sandbox.plan_root, "index.md"),
            "document": read_document(sandbox.plan_root, "plan.md"),
        },
        "memory": {"scope": "shared_session_notes", "notes": notes,
                   "truncated": truncated, "error": memory_error},
    }
