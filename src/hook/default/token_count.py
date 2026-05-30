"""TokenCountHook — counts tokens in the current message list and logs the result.

Fires on PRE_MESSAGES. Never blocks or modifies messages.
"""

from __future__ import annotations

from src.logger import logger
from src.utils import count_message_tokens
from src.hook.types import HookContext, HookResult, Hook
from src.registry import HOOK


@HOOK.register_module(force=True)
class TokenCountHook(Hook):
    name: str = "token_count"
    description: str = "Counts total tokens in the current message list and logs the result."
    priority: int = 10

    model_name: str = ""

    async def handle(self, ctx: HookContext) -> HookResult:
        messages = ctx.input.get("messages") if ctx.input else None
        if not messages:
            return HookResult.allow()

        msg_dicts = []
        for m in messages:
            if hasattr(m, "model_dump"):
                d = m.model_dump()
                if isinstance(d.get("content"), list):
                    parts = d["content"]
                    d["content"] = "\n".join(
                        p.get("text", "") for p in parts
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                msg_dicts.append(d)

        token_count = count_message_tokens(msg_dicts, model=self.model_name)
        logger.debug(f"| 📊 [{ctx.id}] Token count: {token_count}")
        return HookResult.allow()
