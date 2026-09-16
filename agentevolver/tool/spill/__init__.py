"""Where an oversized tool result is parked, and how a caller reaches it again.

A `SpillManagerServer` class stood here, holding one field — the active store — and
forwarding one method to it. A manager earns its name by managing several instances of
something; this managed one, and the class was the only reason `spill` was a top-level
module rather than part of the pipeline that uses it. Both are gone: the store is a module
value with a swap function, and spilling lives under `tool/`, where it is used.

The store stays swappable. Point `use_store` at a remote or object-store backend and every
tool follows, because a tool never names a backend — it calls `save_text` and gets a
reference or `None`.
"""

from __future__ import annotations

from typing import Optional

from agentevolver.tool.spill.types import SpillRef, SpillSource, SpillStore

#: The active store. Module-level rather than a manager attribute: there is one, and
#: "which one" is a deployment decision, not a set to be managed.
_store: Optional[SpillStore] = None


def use_store(store: Optional[SpillStore]) -> None:
    """Install the store every later spill goes through.

    ``None`` restores the default on the next call, which is what a test wants after
    installing a broken store — otherwise the substitution leaks into the rest of the run.
    """
    global _store
    _store = store


def store() -> SpillStore:
    """The active store, defaulting to the local one on first use."""
    global _store
    if _store is None:
        from agentevolver.tool.spill.default import LocalSpillStore

        _store = LocalSpillStore()
    return _store


async def save_text(
    content: str,
    source: SpillSource,
    *,
    session_key: Optional[str] = None,
    suggested_name: str = "output.txt",
) -> Optional[SpillRef]:
    """Save ``content`` and return its reference, or ``None`` if it could not be saved.

    Best-effort by construction, and that is why the failure is swallowed here rather than
    at the two call sites. The caller is holding a tool result that has already been
    produced: a store that is down or a disk that is full is a reason to lose the
    transcript, not a reason to report the command as having failed. The failure is logged
    where an operator will see it, and the caller keeps whatever inline excerpt it had.
    """
    from agentevolver.logger import logger

    try:
        return await store().save_text(
            content, source, session_key=session_key, suggested_name=suggested_name
        )
    except Exception as exc:  # noqa: BLE001 — a storage failure must not fail the call
        logger.warning(
            f"| ⚠️ Could not spill {len(content):,} characters from {source.tool_name}: {exc}"
        )
        return None


__all__ = ["SpillRef", "SpillSource", "SpillStore", "save_text", "store", "use_store"]
