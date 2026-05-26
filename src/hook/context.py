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


# Model pricing per million tokens (USD) — approximate, updated 2025
_MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "haiku":  {"input": 0.80,  "output": 4.00,  "cache_write": 1.00,  "cache_read": 0.08},
    "sonnet": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30},
    "opus":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "gpt-4o": {"input": 2.50,  "output": 10.00, "cache_write": 0.0,   "cache_read": 1.25},
    "o3":     {"input": 10.00, "output": 40.00, "cache_write": 0.0,   "cache_read": 2.50},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}


def _get_pricing(model_name: str) -> Dict[str, float]:
    name = model_name.lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in name:
            return pricing
    return _DEFAULT_PRICING


class UsageAccumulator(BaseModel):
    """Accumulates token usage and estimates cost across all LLM calls in a session."""
    total_input: int = 0
    total_output: int = 0
    total_cache_write: int = 0
    total_cache_read: int = 0
    turn_count: int = 0

    def add(self, input_tokens: int = 0, output_tokens: int = 0,
            cache_write: int = 0, cache_read: int = 0) -> None:
        self.total_input += input_tokens
        self.total_output += output_tokens
        self.total_cache_write += cache_write
        self.total_cache_read += cache_read
        self.turn_count += 1

    def cost_usd(self, model_name: str = "") -> float:
        p = _get_pricing(model_name)
        M = 1_000_000
        return (
            self.total_input       * p["input"]       / M +
            self.total_output      * p["output"]      / M +
            self.total_cache_write * p["cache_write"] / M +
            self.total_cache_read  * p["cache_read"]  / M
        )

    def summary_line(self, model_name: str = "") -> str:
        cost = self.cost_usd(model_name)
        return (
            f"turns={self.turn_count} "
            f"in={self.total_input} out={self.total_output} "
            f"cache_w={self.total_cache_write} cache_r={self.total_cache_read} "
            f"~${cost:.4f}"
        )


class HookSessionState(BaseModel):
    """Mutable state that hooks can read/write, scoped to one session."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    session_id: str

    # Token tracking (written by TokenCountHook)
    last_token_count: int = 0
    peak_token_count: int = 0

    # Summary state (written by CompactHook)
    summary_text: Optional[str] = None
    summary_covers_steps: int = 0
    last_summary_step: int = 0

    # File tracking (written by CodeAgent or PostAction hooks)
    modified_files: Set[str] = Field(default_factory=set)
    read_files: Set[str] = Field(default_factory=set)

    # Accumulated token usage and cost across all LLM calls in this session
    usage: UsageAccumulator = Field(default_factory=UsageAccumulator)

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
