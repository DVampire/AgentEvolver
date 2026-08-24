"""The project's ``.ipynb`` files, and how the kernel's history becomes one.

Notebooks belong here rather than beside the panel that shows them because both
things they touch are the kernel's: the history a save writes out, and the
workspace the Lab already serves. A separate module owning them would have to
reach into `kernel_manager` for the first and `path_manager` for the second, and
would then be an intermediary with nothing of its own.

There is deliberately no in-memory list of notebooks. A notebook is a file, and a
file the agent wrote, the user saved in JupyterLab, or a training run dropped in
the workspace all have to appear the same way — which is only true if the listing
is a directory scan every time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

from agentevolver.kernel.server import kernel_manager
from agentevolver.kernel.types import Notebook
from agentevolver.paths import P, path_manager


def directory(session_id: str, *, owner: str = "local"):
    """The project's notebook directory, created if this is the first look."""
    return path_manager.get(P.SESSION_NOTEBOOKS, owner=owner, session_id=session_id, create=True)


def notebooks(session_id: str, *, owner: str = "local") -> List[Notebook]:
    """Every ``.ipynb`` in the project's workspace, newest first."""
    workspace = path_manager.get(P.SESSION_WORKSPACE, owner=owner, session_id=session_id)
    if not workspace.is_dir():
        return []
    found: List[Notebook] = []
    for path in workspace.rglob("*.ipynb"):
        if ".ipynb_checkpoints" in path.parts:
            continue
        try:
            stat = path.stat()
            cells = len(json.loads(path.read_text(encoding="utf-8")).get("cells", []))
        except (OSError, ValueError):
            continue  # unreadable or mid-write; it will appear next refresh
        found.append(Notebook(
            path=str(path.relative_to(workspace)), title=path.stem,
            size_bytes=stat.st_size, cell_count=cells,
            modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        ))
    return sorted(found, key=lambda item: item.modified_at, reverse=True)


def save_history_as_notebook(session_id: str, name: str, *, owner: str = "local") -> Notebook:
    """Write what has run in this project's kernel out as a real ``.ipynb``.

    The panel is the kernel's live history, not a document; this is how you keep
    a copy — openable in JupyterLab, in the Code view, or by anything else that
    reads notebooks.
    """
    target = directory(session_id, owner=owner)
    stem = "".join(c for c in (name or "session") if c.isalnum() or c in " -_").strip() or "session"
    path = target / f"{stem}.ipynb"
    counter = 2
    while path.exists():
        path = target / f"{stem}-{counter}.ipynb"
        counter += 1

    cells = [{
        "cell_type": "code", "id": f"c{index}",
        "source": entry.code,
        "execution_count": entry.execution_count,
        "metadata": {"agentevolver": {"origin": entry.origin, "started_at": entry.started_at}},
        "outputs": [_to_nbformat(output) for output in entry.outputs],
    } for index, entry in enumerate(kernel_manager.history(session_id, limit=1000))]

    path.write_text(json.dumps({
        "cells": cells, "nbformat": 4, "nbformat_minor": 5, "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        },
    }, indent=1), encoding="utf-8")
    workspace = path_manager.get(P.SESSION_WORKSPACE, owner=owner, session_id=session_id)
    return Notebook(path=str(path.relative_to(workspace)), title=path.stem,
                    size_bytes=path.stat().st_size, cell_count=len(cells),
                    modified_at=datetime.now(timezone.utc).isoformat())


def _to_nbformat(output) -> dict:
    """One KernelOutput as nbformat, so the saved file is a valid notebook."""
    data = dict(output.data or {})
    if output.type == "stream":
        return {"output_type": "stream", "name": output.name or "stdout",
                "text": data.get("text/plain", "")}
    if output.type == "error":
        text = data.get("text/plain", "")
        # nbformat requires all three; only the traceback was carried, so the
        # name and value are derived from its last line rather than invented.
        last = text.strip().splitlines()[-1] if text.strip() else "Error"
        ename, _, evalue = last.partition(":")
        return {"output_type": "error", "ename": ename.strip() or "Error",
                "evalue": evalue.strip(), "traceback": text.splitlines()}
    if output.type == "result":
        return {"output_type": "execute_result", "data": data, "metadata": {}, "execution_count": None}
    return {"output_type": "display_data", "data": data, "metadata": {}}


__all__ = ["directory", "notebooks", "save_history_as_notebook"]
