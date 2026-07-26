"""Redis — from the Langflow `redis` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class RedisRedisPlugin(VectorStorePlugin):
    name: str = "redis.redis"
    display_name: str = 'Redis'
    description: str = 'Implementation of Vector Store using Redis'
    kind: str = "vectorstore"
    bundle: str = "redis"
    bundle_label: str = 'Redis'
    source: str = "langflow/bundles/redis"
    status: str = "complete"
    needs_embedding: bool = True

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_community.vectorstores.redis import Redis
        return Redis(redis_url=conn.get("redis_url") or "redis://localhost:6379",
                     index_name=conn.get("index_name") or "langflow", embedding=embedding)

    async def __call__(self, index_name: str = "langflow", redis_url: str = "redis://localhost:6379", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            index_name=index_name, redis_url=redis_url)
