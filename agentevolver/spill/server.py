"""The spill manager: one store, and the policy question of when to use it."""

from __future__ import annotations

from typing import Optional

from agentevolver.logger import logger
from agentevolver.spill.types import SpillRef, SpillSource, SpillStore


class SpillManagerServer:
    """Hold the active spill store and answer the one question tools ask of it.

    The store is swappable — point this at a remote or object-store backend and
    every tool follows, because tools never name a backend. Nothing registers
    through a Registry: spill is infrastructure the framework writes to, like the
    sandbox, not a capability an agent evolves.
    """

    def __init__(self) -> None:
        self._store: Optional[SpillStore] = None

    def register(self, store: SpillStore) -> None:
        """Install the store every later spill goes through."""
        self._store = store

    @property
    def store(self) -> SpillStore:
        """The active store, defaulting to the local one on first use."""
        if self._store is None:
            from agentevolver.spill.default import LocalSpillStore

            self._store = LocalSpillStore()
        return self._store

    async def save_text(
        self,
        content: str,
        source: SpillSource,
        *,
        session_key: Optional[str] = None,
        suggested_name: str = "output.txt",
    ) -> Optional[SpillRef]:
        """Save ``content`` and return its reference, or ``None`` if it could not be saved.

        Best-effort by construction. The caller is holding a tool result that has
        already been produced; a store that is down or a disk that is full is a
        reason to lose the transcript, not a reason to report the command as having
        failed. The failure is logged where an operator will see it, and the caller
        keeps whatever inline excerpt it already had.
        """
        try:
            return await self.store.save_text(
                content, source, session_key=session_key, suggested_name=suggested_name
            )
        except Exception as exc:  # noqa: BLE001 — a storage failure must not fail the call
            logger.warning(
                f"| ⚠️ Could not spill {len(content):,} characters from {source.tool_name}: {exc}"
            )
            return None


#: Global spill manager instance
spill_manager = SpillManagerServer()

__all__ = ["SpillManagerServer", "spill_manager"]
