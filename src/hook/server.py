"""HookManager — singleton that registers hooks and fires events.

Design decisions:
- Hook instances are shared across sessions (stateless by design).
  Per-session mutable state lives in HookContextManager.
- Handlers for a given event run concurrently (asyncio.gather), then
  results are merged with _merge_results (most-restrictive wins).
- Per-session asyncio.Lock (inside HookSessionState) ensures that
  hooks writing shared session state do so atomically.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from src.logger import logger
from src.utils import Singleton
from src.hook.types import (
    HookEvent,
    HookContext,
    HookResult,
    Hook,
    _merge_results,
)
from src.hook.context import HookContextManager


class HookManager(metaclass=Singleton):
    """Singleton registry and dispatcher for all hooks."""

    def __init__(self) -> None:
        self._hooks: List[Hook] = []
        self.context = HookContextManager()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, hook: Hook) -> None:
        """Register a hook instance. Replaces any existing instance with the same name."""
        self._hooks = [h for h in self._hooks if h.name != hook.name]
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)
        logger.info(f"| 🔌 Hook registered: {hook.name} (priority={hook.priority})")

    def unregister(self, name: str) -> None:
        self._hooks = [h for h in self._hooks if h.name != name]

    def get(self, name: str) -> Optional[Hook]:
        for h in self._hooks:
            if h.name == name:
                return h
        return None

    def list(self) -> List[str]:
        return [h.name for h in self._hooks]

    # ------------------------------------------------------------------
    # Hook dispatch
    # ------------------------------------------------------------------

    async def __call__(self, ctx: HookContext) -> HookResult:
        """Dispatch a hook event to all matching hooks, concurrently.

        Returns a merged HookResult. Hooks that raise are logged and
        treated as ALLOW (non-fatal).
        """
        candidates = [h for h in self._hooks if h.handles_event(ctx.event)]
        if not candidates:
            return HookResult.allow()

        # Attach session state to extra so hooks can read/write it
        session_state = await self.context.get_or_create(ctx.id)
        ctx.extra["_session_state"] = session_state

        async def _safe_handle(h: Hook) -> HookResult:
            try:
                return await h.handle(ctx)
            except Exception as e:
                logger.warning(f"| ⚠️ Hook '{h.name}' raised on {ctx.event}: {e}")
                return HookResult.allow()

        results = await asyncio.gather(*[_safe_handle(h) for h in candidates])
        merged = _merge_results(list(results))

        if merged.additional_context:
            logger.debug(f"| 💬 Hook additional_context injected ({len(merged.additional_context)} chars)")

        return merged

    # ------------------------------------------------------------------
    # Session lifecycle helpers
    # ------------------------------------------------------------------

    async def start_session(self, session_id: str) -> None:
        await self.context.get_or_create(session_id)

    async def end_session(self, session_id: str) -> None:
        await self.context.end_session(session_id)


hook_manager = HookManager()
