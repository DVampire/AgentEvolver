"""Shared task-input helpers for the example run scripts.

Lets every ``examples/run_*.py`` accept a task as either an inline ``--task``
string or a ``--task-file`` document (.html / .md) under ``examples/tasks/``,
without each script duplicating the load + render logic.

Priority: ``--task`` string  >  ``--task-file`` document  >  the script's own
default text.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from agentevolver.task.loader import load_task_document
from agentevolver.visual import render_task_page


def add_task_args(parser, default_task_file: Optional[str] = None) -> None:
    """Add ``--task`` / ``--task-file`` to a run script's argparse parser."""
    parser.add_argument(
        "--task", default=None,
        help="Inline task string (overrides --task-file).",
    )
    parser.add_argument(
        "--task-file", default=default_task_file,
        help="Path to a task document (.html or .md) under examples/tasks/.",
    )


def resolve_task(
    args, task_work_dir: str, default_text: Optional[str] = None,
) -> Tuple[Optional[str], Optional[List[str]], Optional[Dict[str, Any]]]:
    """Resolve the task for a run script.

    Returns ``(content, files, metadata)`` ready to splat into
    ``task_manager.submit(content=..., files=..., metadata=...)``. When a task
    document is used, its clean text becomes ``content``, its path goes into
    ``files``, and a styled HTML view is rendered to ``task_work_dir`` (path in
    ``metadata``).
    """
    if getattr(args, "task", None):
        return args.task, None, None

    task_file = getattr(args, "task_file", None)
    if task_file:
        doc = load_task_document(task_file)
        view_path = os.path.join(task_work_dir, "task_view.html")
        render_task_page(doc.html_body, view_path, title=doc.title)
        meta = {"task_doc": doc.source_path, "task_view": view_path, "task_kind": doc.kind}
        return doc.content, [doc.source_path], meta

    return default_text, None, None
