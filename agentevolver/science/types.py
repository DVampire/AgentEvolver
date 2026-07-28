"""Wire/state models for the Science workstation.

A **workstation** is one JupyterLab container per project, with the host's GPUs
attached. It is a peer container, not the base environment: the agent and its
tools run in base, which stays lean, while this is where open-ended work
happens — training a model, running a sweep, writing the paper.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ComputeStatus(BaseModel):
    """What the workstation is running on, as the Compute panel shows it."""

    model_config = ConfigDict(extra="ignore")

    running: bool = False
    #: Populated from nvidia-smi *inside* the container: what it can actually
    #: use, which is what --gpus granted it, not what the host happens to own.
    gpus: List[Dict[str, Any]] = Field(default_factory=list)
    cpu_count: Optional[int] = None
    memory_total_mb: Optional[int] = None
    memory_used_mb: Optional[int] = None
    disk_free_mb: Optional[int] = None
    uptime_seconds: float = 0.0


class Notebook(BaseModel):
    """One ``.ipynb`` in the project's workspace.

    Kept as a real notebook file rather than a private format so the same
    document opens in the embedded JupyterLab, in the Code view's editor, and in
    anything the user later runs over the workspace.
    """

    model_config = ConfigDict(extra="ignore")

    #: Path relative to the workspace root, e.g. ``notebooks/analysis.ipynb``.
    path: str
    title: str = ""
    size_bytes: int = 0
    modified_at: str = ""
    cell_count: int = 0


class Cell(BaseModel):
    """One cell of a notebook, as the Science view edits it.

    A projection of what Jupyter's contents API returns — the gateway never
    writes the ``.ipynb`` itself. The Jupyter Server in the workstation owns the
    document; this view is one of its clients, exactly like the embedded Lab is.
    Two writers of the same file is how edits get silently lost.

    Normalised rather than passed through raw because nbformat spells the same
    things differently: ``source`` is a string *or* a list of lines, and an
    output is ``{output_type, text}`` where the rest of this codebase says
    ``{type, data}``. Converting at the edge means one renderer serves a Science
    cell and a ``code_interpreter_tool`` result alike.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    #: ``code`` or ``markdown``. nbformat also has ``raw``; it round-trips
    #: untouched rather than being offered for editing.
    type: str = "code"
    source: str = ""
    #: What the kernel produced, in MIME-bundle form — the same shape
    #: ``code_interpreter_tool`` returns, so one renderer serves both.
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
    execution_count: Optional[int] = None


class ScienceInstance(BaseModel):
    """One running workstation container, bound to a single project."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    session_id: str = Field(description="Project this workstation belongs to.")
    owner: str = Field(default="local")
    upstream: str = Field(description="Base URL of the container's published Lab port on loopback.")
    base_path: str = Field(default="", description="Sub-path the Lab is served under on the UI origin, e.g. /science/<session>.")
    workspace_root: str = Field(description="Host directory mounted at /workspace.")
    started_at: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time, description="Last heartbeat/proxy touch, for idle reaping.")
    gpus: str = Field(default="", description="What --gpus this container was given; empty when it has none.")

    #: The live ScienceSandbox handle. Excluded from serialisation.
    sandbox: Optional[Any] = Field(default=None, exclude=True)

    def public(self) -> Dict[str, Any]:
        """Client-visible status (never leaks the internal upstream URL)."""
        return {
            "session_id": self.session_id,
            "running": True,
            # Where the UI embeds the Lab, relative to its own origin — so it
            # works over any tunnel without a hostname of its own. The BASE,
            # matching JupyterLab's --ServerApp.base_url: the caller appends
            # /lab for the workbench or /api/... for the server. Returning the
            # /lab form here had the view append its own and ask for /lab/lab,
            # which the Lab's catch-all answers 200 with the same HTML — a
            # broken URL that looks like a working one.
            "path": self.base_path,
            "started_at": self.started_at,
            "idle_seconds": round(time.time() - self.last_seen, 1),
            "workspace_root": self.workspace_root,
            "gpus": self.gpus,
        }


__all__ = ["ComputeStatus", "Notebook", "ScienceInstance"]
