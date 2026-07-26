"""Valkey — from the Langflow `valkey` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class ValkeyValkeyPlugin(VectorStorePlugin):
    name: str = "valkey.valkey"
    display_name: str = 'Valkey'
    description: str = 'Implementation of Vector Store using Valkey'
    kind: str = "vectorstore"
    bundle: str = "valkey"
    bundle_label: str = 'Valkey'
    source: str = "langflow/bundles/valkey"
    status: str = "complete"
    needs_embedding: bool = True

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_community.vectorstores.redis import Redis
        return Redis(redis_url=conn.get("valkey_url") or "redis://localhost:6379",
                     index_name=conn.get("index_name") or "langflow", embedding=embedding)

    async def __call__(self, index_name: str = "langflow", valkey_url: str = "redis://localhost:6379", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            index_name=index_name, valkey_url=valkey_url)
