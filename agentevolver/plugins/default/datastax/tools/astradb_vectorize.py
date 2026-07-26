"""Astra Vectorize — from the Langflow `datastax` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class DatastaxAstradbVectorizePlugin(VectorStorePlugin):
    name: str = "datastax.astradb_vectorize"
    display_name: str = 'Astra Vectorize'
    description: str = 'Configuration options for Astra Vectorize server-side embeddings. '
    kind: str = "vectorstore"
    bundle: str = "datastax"
    bundle_label: str = 'Astra DB'
    source: str = "langflow/bundles/datastax"
    status: str = "complete"
    needs_embedding: bool = False

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_astradb import AstraDBVectorStore
        from astrapy.info import VectorServiceOptions
        token = self._secret(conn.get("token"), "ASTRA_DB_APPLICATION_TOKEN")
        endpoint = conn.get("api_endpoint") or self._secret("", "ASTRA_DB_API_ENDPOINT")
        if not token or not endpoint:
            raise ValueError("Astra DB needs a token and api_endpoint.")
        # Vectorize = server-side embeddings (no external embedding model).
        return AstraDBVectorStore(collection_name=conn.get("collection_name") or "langflow",
                                  token=token, api_endpoint=endpoint,
                                  collection_vector_service_options=VectorServiceOptions(provider="nvidia", model_name="NV-Embed-QA"))

    async def __call__(self, collection_name: str = "langflow", token: str = "", api_endpoint: str = "", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            collection_name=collection_name, token=token, api_endpoint=api_endpoint)
