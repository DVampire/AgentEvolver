"""Redis Chat Memory — from the Langflow `redis` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import MemoryPlugin


@PLUGIN.register_module(force=True)
class RedisRedisChatPlugin(MemoryPlugin):
    name: str = "redis.redis_chat"
    display_name: str = 'Redis Chat Memory'
    description: str = 'Retrieves and store chat messages from Redis.'
    kind: str = "memory"
    bundle: str = "redis"
    bundle_label: str = 'Redis'
    category: str = "agent"
    source: str = "langflow/bundles/redis"
    status: str = "complete"

    def _history(self, session_id: str, **cfg: Any) -> Any:
        from langchain_community.chat_message_histories import RedisChatMessageHistory
        return RedisChatMessageHistory(session_id=session_id,
                                       url=cfg.get("redis_url") or "redis://localhost:6379")

    async def __call__(self, action: str = "get", session_id: str = "default", message: str = "",
                       role: str = "user", redis_url: str = "redis://localhost:6379", **kwargs) -> Response:
        return await self._memory(action=action, session_id=session_id, message=message, role=role,
                                  **{k: v for k, v in kwargs.items()})
