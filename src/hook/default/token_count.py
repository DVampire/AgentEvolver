"""TokenCountHook — counts tokens and updates session state.

Fires on PRE_MESSAGES. Never blocks or modifies messages.
Writes last_token_count and peak_token_count to session state.
"""

from __future__ import annotations

from src.logger import logger
from src.utils import count_message_tokens
from src.hook.types import HookContext, HookEvent, HookResult, Hook
from src.hook.context import HookSessionState
from src.registry import HOOK


@HOOK.register_module(force=True)
class TokenCountHook(Hook):
    name: str = "token_count"
    description: str = "Counts total tokens in the current message list and updates session state."
    events: list = [HookEvent.PRE_MESSAGES]
    priority: int = 10

    model_name: str = ""

    async def handle(self, ctx: HookContext) -> HookResult:
        if not ctx.messages:
            return HookResult.allow()

        state: HookSessionState = ctx.extra.get("_session_state")
        if state is None:
            return HookResult.allow()

        msg_dicts = []
        for m in ctx.messages:
            if hasattr(m, "model_dump"):
                d = m.model_dump()
                if isinstance(d.get("content"), list):
                    parts = d["content"]
                    text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
                    d["content"] = "\n".join(text_parts)
                msg_dicts.append(d)

        token_count = count_message_tokens(msg_dicts, model=self.model_name)

        async with state.get_lock():
            state.last_token_count = token_count
            if token_count > state.peak_token_count:
                state.peak_token_count = token_count

        logger.debug(f"| 📊 [{ctx.id}] Token count: {token_count} (peak: {state.peak_token_count})")
        return HookResult.allow()
