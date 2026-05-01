"""HookContextManager — per-session state store.

Each session gets its own isolated HookSessionState so concurrent
agent runs never share mutable state. Locks are per-session.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.message import Message


class HookSessionState(BaseModel):
    """Mutable state that hooks can read/write, scoped to one session."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    session_id: str

    # Token tracking (written by TokenCountHook)
    last_token_count: int = 0
    peak_token_count: int = 0

    # Summary state (written by HistorySummaryHook)
    summary_text: Optional[str] = None
    summary_covers_steps: int = 0
    last_summary_step: int = 0

    # File tracking (written by CodeAgent or PostAction hooks)
    modified_files: Set[str] = Field(default_factory=set)
    read_files: Set[str] = Field(default_factory=set)

    # Arbitrary per-hook scratch space keyed by hook name
    scratch: Dict[str, Any] = Field(default_factory=dict)

    # Internal asyncio lock — not serialised
    _lock: Optional[asyncio.Lock] = None

    def get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock


class HookContextManager:
    """Manages per-session HookSessionState with async-safe access.

    Sessions are created on first access and cleaned up explicitly via
    end_session(). Embedded inside HookManager (singleton).
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, HookSessionState] = {}
        self._global_lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> HookSessionState:
        """Return the session state, creating it if this is the first access."""
        async with self._global_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = HookSessionState(session_id=session_id)
                logger.debug(f"| 🔧 Hook session created: {session_id}")
            return self._sessions[session_id]

    async def get(self, session_id: str) -> Optional[HookSessionState]:
        """Return the session state without creating it."""
        return self._sessions.get(session_id)

    async def end_session(self, session_id: str) -> None:
        """Clean up session state when the agent finishes."""
        async with self._global_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.debug(f"| 🧹 Hook session cleaned up: {session_id}")

    async def update(self, session_id: str, **kwargs: Any) -> None:
        """Atomically update fields on a session state."""
        state = await self.get_or_create(session_id)
        async with state.get_lock():
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)

    def list_sessions(self) -> List[str]:
        return list(self._sessions.keys())
