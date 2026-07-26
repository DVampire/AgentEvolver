"""Valkey Chat Memory — from the Langflow `valkey` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import MemoryPlugin


@PLUGIN.register_module(force=True)
class ValkeyValkeyChatPlugin(MemoryPlugin):
    name: str = "valkey.valkey_chat"
    display_name: str = 'Valkey Chat Memory'
    description: str = 'Retrieves and stores chat messages from Valkey.'
    kind: str = "memory"
    bundle: str = "valkey"
    bundle_label: str = 'Valkey'
    category: str = "agent"
    source: str = "langflow/bundles/valkey"
    status: str = "complete"

    def _history(self, session_id: str, **cfg: Any) -> Any:
        from langchain_community.chat_message_histories import RedisChatMessageHistory
        return RedisChatMessageHistory(session_id=session_id,
                                       url=cfg.get("valkey_url") or "redis://localhost:6379")

    async def __call__(self, action: str = "get", session_id: str = "default", message: str = "",
                       role: str = "user", valkey_url: str = "redis://localhost:6379", **kwargs) -> Response:
        return await self._memory(action=action, session_id=session_id, message=message, role=role,
                                  **{k: v for k, v in kwargs.items()})
