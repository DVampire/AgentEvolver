"""MongoDB Atlas — from the Langflow `mongodb` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class MongodbMongodbAtlasPlugin(VectorStorePlugin):
    name: str = "mongodb.mongodb_atlas"
    display_name: str = 'MongoDB Atlas'
    description: str = 'MongoDB Atlas Vector Store with search capabilities'
    kind: str = "vectorstore"
    bundle: str = "mongodb"
    bundle_label: str = 'MongoDB Atlas'
    source: str = "langflow/bundles/mongodb"
    status: str = "complete"
    needs_embedding: bool = True

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from pymongo import MongoClient
        from langchain_mongodb import MongoDBAtlasVectorSearch
        for req in ("connection_string", "database_name", "collection_name"):
            if not conn.get(req):
                raise ValueError(f"MongoDB Atlas needs '{req}'.")
        client = MongoClient(conn["connection_string"])
        collection = client[conn["database_name"]][conn["collection_name"]]
        return MongoDBAtlasVectorSearch(collection=collection, embedding=embedding,
                                        index_name=conn.get("index_name") or "vector_index")

    async def __call__(self, connection_string: str = "", database_name: str = "", collection_name: str = "", index_name: str = "vector_index", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            connection_string=connection_string, database_name=database_name,
            collection_name=collection_name, index_name=index_name)
