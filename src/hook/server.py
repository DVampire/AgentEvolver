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
import inflection
from typing import Dict, List, Optional, Type

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
    # Initialization — auto-discover from HOOK registry
    # ------------------------------------------------------------------

    async def initialize(self, hook_names: Optional[List[str]] = None) -> None:
        """Discover all @HOOK.register_module classes, instantiate them with config,
        and register. Hooks already registered by name are skipped.

        Args:
            hook_names: If provided, only register hooks whose class name (underscored)
                        is in this list. None means register all discovered hooks.
        """
        import src.hook.default  # ensure all default hooks are imported/registered
        from src.registry import HOOK
        from src.config import config

        hook_classes: List[Type[Hook]] = list(HOOK._module_dict.values())
        logger.info(f"| 🔍 Discovering {len(hook_classes)} hooks from HOOK registry")

        for hook_cls in hook_classes:
            key = inflection.underscore(hook_cls.__name__)
            if hook_names is not None and key not in hook_names:
                continue
            hook_cfg: dict = getattr(config, key, {})
            try:
                instance = hook_cls(**hook_cfg) if hook_cfg else hook_cls()
                self.register(instance)
            except Exception as e:
                logger.error(f"| ❌ Failed to instantiate hook {hook_cls.__name__}: {e}")

        logger.info(f"| ✅ Hooks initialized: {self.list()}")

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

    async def __call__(
        self,
        ctx,                          # SessionContext, HookContext, or any object with .id
        event: Optional[HookEvent] = None,
        agent_name: str = "",
        step_number: Optional[int] = None,
        action: Optional[dict] = None,
        action_result: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> HookResult:
        """Dispatch a hook event.

        Accepts either:
        - A pre-built HookContext as the first argument (event omitted), or
        - A plain SessionContext + explicit event keyword/positional.

        Returns a merged HookResult. Hooks that raise are logged and
        treated as ALLOW (non-fatal).
        """
        if isinstance(ctx, HookContext):
            hook_ctx = ctx
        else:
            if event is None:
                raise ValueError("event is required when ctx is not a HookContext")
            hook_ctx = HookContext(
                id=ctx.id,
                event=event,
                agent_name=agent_name,
                step_number=step_number,
                action=action,
                action_result=action_result,
                extra=extra or {},
            )
        ctx = hook_ctx
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

        # Auto-cleanup: release per-session state after ON_STOP so it doesn't
        # accumulate indefinitely. All hooks have already run by this point.
        if ctx.event == HookEvent.ON_STOP:
            await self.context.end_session(ctx.id)

        return merged


hook_manager = HookManager()
