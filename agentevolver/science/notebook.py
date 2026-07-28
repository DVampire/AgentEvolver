"""Read and write a notebook through the workstation's Jupyter Server.

The gateway does not write ``.ipynb`` files. The Jupyter Server inside the
science container owns the document, and this module is a client of its
contents API — the same API the embedded JupyterLab uses. Two writers of one
file is how edits get silently lost: whoever saves last wins, and neither side
knows it happened.

The one thing still read straight off disk is the notebook *list*, which is a
pure read and has to answer before the container has booted.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agentevolver.kernel.types import KernelOutput
from agentevolver.science.types import Cell


class NotebookConflict(RuntimeError):
    """The file changed underneath us since it was read.

    Jupyter's server does not check this — JupyterLab does it client-side by
    comparing the ``last_modified`` it holds against what the server reports,
    and so do we, so both clients behave the same way.
    """


def _text(value: Any, *, sep: str = "") -> str:
    """nbformat stores multi-line strings as either a string or a list of lines.

    ``sep`` because the two list forms differ: ``source`` and ``text`` are lines
    that *carry* their newline, while ``traceback`` is a list of already-formatted
    lines that do not. Joining a traceback with "" glues it into one line.
    """
    if isinstance(value, list):
        return sep.join(str(part) for part in value)
    return "" if value is None else str(value)


def output_from_nbformat(raw: Dict[str, Any]) -> Optional[KernelOutput]:
    """One nbformat output as the shape the rest of the codebase uses."""
    kind = raw.get("output_type")
    if kind == "stream":
        return KernelOutput(type="stream", name=raw.get("name"),
                            data={"text/plain": _text(raw.get("text"))})
    if kind in ("execute_result", "display_data"):
        return KernelOutput(type="result" if kind == "execute_result" else "display",
                            data=dict(raw.get("data") or {}))
    if kind == "error":
        traceback = _text(raw.get("traceback"), sep="\n")
        return KernelOutput(type="error", data={
            "text/plain": traceback or f"{raw.get('ename')}: {raw.get('evalue')}"})
    return None


def output_to_nbformat(output: Dict[str, Any]) -> Dict[str, Any]:
    """The inverse, so what we save stays a valid notebook."""
    kind, data = output.get("type"), dict(output.get("data") or {})
    if kind == "stream":
        return {"output_type": "stream", "name": output.get("name") or "stdout",
                "text": data.get("text/plain", "")}
    if kind == "error":
        text = data.get("text/plain", "")
        # nbformat requires all three; a traceback is the only one we carried,
        # so ename/evalue are derived rather than invented.
        first = text.strip().splitlines()[-1] if text.strip() else "Error"
        ename, _, evalue = first.partition(":")
        return {"output_type": "error", "ename": ename.strip() or "Error",
                "evalue": evalue.strip(), "traceback": text.splitlines()}
    if kind == "result":
        return {"output_type": "execute_result", "data": data, "metadata": {},
                "execution_count": output.get("execution_count")}
    return {"output_type": "display_data", "data": data, "metadata": {}}


def cells_from_nbformat(document: Dict[str, Any]) -> List[Cell]:
    cells: List[Cell] = []
    for index, raw in enumerate(document.get("cells") or []):
        outputs = [output.model_dump(mode="json")
                   for output in (output_from_nbformat(item) for item in raw.get("outputs") or [])
                   if output is not None]
        cells.append(Cell(
            # nbformat 4.5 gives every cell an id; older documents have none, so
            # fall back to the position — enough to match on for one save.
            id=str(raw.get("id") or f"cell-{index}"),
            type=str(raw.get("cell_type") or "code"),
            source=_text(raw.get("source")),
            outputs=outputs,
            execution_count=raw.get("execution_count"),
        ))
    return cells


def merge_cells(document: Dict[str, Any], cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Put edited cells back into the document Jupyter just gave us.

    Merged into the existing document rather than rebuilt from our own model:
    nbformat carries per-cell metadata, attachments and notebook-level settings
    this view does not model, and rebuilding would quietly delete all of it.
    """
    existing = {str(raw.get("id")): raw for raw in document.get("cells") or [] if raw.get("id")}
    merged: List[Dict[str, Any]] = []
    for incoming in cells:
        cell_id = str(incoming.get("id") or "")
        base = dict(existing.get(cell_id) or {"id": cell_id, "metadata": {}})
        base["cell_type"] = incoming.get("type") or base.get("cell_type") or "code"
        base["source"] = incoming.get("source") or ""
        if base["cell_type"] == "code":
            base["outputs"] = [output_to_nbformat(item) for item in incoming.get("outputs") or []]
            base["execution_count"] = incoming.get("execution_count")
        else:
            # A markdown cell that carries outputs is not a valid notebook.
            base.pop("outputs", None)
            base.pop("execution_count", None)
        merged.append(base)
    document["cells"] = merged
    return document


class NotebookClient:
    """Jupyter's contents API for one workstation."""

    def __init__(self, base_url: str) -> None:
        #: e.g. ``http://127.0.0.1:41293/science/<session>``
        self.base_url = base_url.rstrip("/")

    async def get(self, path: str) -> Tuple[List[Cell], str, Dict[str, Any]]:
        """The notebook's cells, its ``last_modified``, and the raw document."""
        import aiohttp

        async with aiohttp.ClientSession() as http:
            async with http.get(f"{self.base_url}/api/contents/{path}",
                                params={"type": "notebook", "content": "1"},
                                timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Could not read {path}: {response.status} {await response.text()}")
                body = await response.json()
        document = body.get("content") or {}
        return cells_from_nbformat(document), str(body.get("last_modified") or ""), document

    async def save(self, path: str, cells: List[Dict[str, Any]],
                   last_modified: str = "") -> str:
        """Write the cells back, refusing if the file moved on beneath us.

        Returns the new ``last_modified`` for the caller to hold until its next
        save — the same handshake JupyterLab runs.
        """
        import aiohttp

        _, current_modified, document = await self.get(path)
        if last_modified and current_modified and last_modified != current_modified:
            raise NotebookConflict(
                f"{path} was changed elsewhere (probably in JupyterLab) since you opened it.")

        merged = merge_cells(document, cells)
        async with aiohttp.ClientSession() as http:
            async with http.put(f"{self.base_url}/api/contents/{path}",
                                json={"type": "notebook", "format": "json", "content": merged},
                                timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Could not save {path}: {response.status} {await response.text()}")
                body = await response.json()
        return str(body.get("last_modified") or "")

    async def create(self, path: str) -> None:
        """Create an empty notebook through the server, so its watchers see it."""
        import aiohttp

        empty = {"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}}}
        async with aiohttp.ClientSession() as http:
            async with http.put(f"{self.base_url}/api/contents/{path}",
                                json={"type": "notebook", "format": "json", "content": empty},
                                timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Could not create {path}: {response.status} {await response.text()}")


__all__ = ["NotebookClient", "NotebookConflict", "cells_from_nbformat", "merge_cells",
           "output_from_nbformat", "output_to_nbformat"]
