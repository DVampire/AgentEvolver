"""Qdrant — from the Langflow `qdrant` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class QdrantQdrantPlugin(VectorStorePlugin):
    name: str = "qdrant.qdrant"
    display_name: str = 'Qdrant'
    description: str = 'Qdrant Vector Store with search capabilities'
    kind: str = "vectorstore"
    bundle: str = "qdrant"
    bundle_label: str = 'Qdrant'
    source: str = "langflow/bundles/qdrant"
    status: str = "complete"

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_qdrant import QdrantVectorStore
        if not conn.get("collection_name"):
            raise ValueError("Qdrant needs a 'collection_name'.")
        kw = {"collection_name": conn["collection_name"], "embedding": embedding}
        if conn.get("url"):
            kw["url"] = conn["url"]
        elif conn.get("host"):
            kw["host"], kw["port"] = conn["host"], int(conn.get("port") or 6333)
        if conn.get("api_key"):
            kw["api_key"] = conn["api_key"]
        return QdrantVectorStore.from_existing_collection(**kw)

    async def __call__(self, collection_name: str = "", url: str = "", api_key: str = "", host: str = "", port: int = 6333, query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            collection_name=collection_name, url=url, api_key=api_key, host=host, port=int(port))
