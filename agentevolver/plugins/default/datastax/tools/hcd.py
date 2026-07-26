"""Hyper-Converged Database — from the Langflow `datastax` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class DatastaxHcdPlugin(VectorStorePlugin):
    name: str = "datastax.hcd"
    display_name: str = 'Hyper-Converged Database'
    description: str = 'Implementation of Vector Store using Hyper-Converged Database (HCD) with search capabilities'
    kind: str = "vectorstore"
    bundle: str = "datastax"
    bundle_label: str = 'HCD'
    source: str = "langflow/bundles/datastax"
    status: str = "complete"
    needs_embedding: bool = True

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_astradb import AstraDBVectorStore
        token = self._secret(conn.get("token"), "ASTRA_DB_APPLICATION_TOKEN", "HCD_TOKEN")
        endpoint = conn.get("api_endpoint") or self._secret("", "HCD_API_ENDPOINT", "ASTRA_DB_API_ENDPOINT")
        if not token or not endpoint:
            raise ValueError("HCD needs a token and api_endpoint.")
        return AstraDBVectorStore(collection_name=conn.get("collection_name") or "langflow",
                                  embedding=embedding, token=token, api_endpoint=endpoint)

    async def __call__(self, collection_name: str = "langflow", token: str = "", api_endpoint: str = "", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            collection_name=collection_name, token=token, api_endpoint=api_endpoint)
