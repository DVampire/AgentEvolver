"""Naming conventions for IDs across the multi-agent system.

Hierarchy:
    session        — top-level session for one user request (owned by MetaAgent)
    task           — logical subtask within a session (owned by a sub-agent)
    subtask_session — isolated execution context for one run of a task
                     format: <session_id>__<task_id>__s<step>
                     step increments on each retry so memory/trace never bleed.
"""

from __future__ import annotations

import uuid
from datetime import datetime


def generate_unique_id(prefix: str = "session") -> str:
    """Generate a unique ID using a timestamp and a short UUID fragment."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    return f"{prefix}_{timestamp}_{unique_id}"


def new_session_id() -> str:
    """New top-level session ID for a MetaAgent-driven user request."""
    return generate_unique_id("session")


def new_task_id() -> str:
    """New logical task ID for a subtask within a session."""
    return generate_unique_id("task")


def subtask_session_id(session_id: str, task_id: str, step: int = 1) -> str:
    """Isolated session ID for one execution of a subtask.

    Each retry uses a different step so no state bleeds between runs.
    Example: session_20240501-120000_abc123__task_20240501-120001_def456__s1
    """
    return f"{session_id}__{task_id}__s{step}"


def parse_subtask_session_id(session_id: str) -> dict:
    """Parse a subtask session ID into its components.

    Returns dict with keys: session_id, task_id, step.
    Returns empty dict if the format does not match.
    """
    parts = session_id.split("__")
    if len(parts) != 3:
        return {}
    sid, tid, step_part = parts
    if not step_part.startswith("s") or not step_part[1:].isdigit():
        return {}
    return {
        "session_id": sid,
        "task_id": tid,
        "step": int(step_part[1:]),
    }
