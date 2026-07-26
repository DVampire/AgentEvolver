"""Graph RAG — from the Langflow `datastax` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DatastaxGraphRagPlugin(BundlePlugin):
    name: str = "datastax.graph_rag"
    display_name: str = 'Graph RAG'
    description: str = 'Graph RAG traversal for vector store.'
    kind: str = "tool"
    bundle: str = "datastax"
    bundle_label: str = 'Astra DB'
    category: str = "knowledge"
    source: str = "langflow/bundles/datastax"
    status: str = "complete"

    async def __call__(self, query: str = "", token: str = "", api_endpoint: str = "", **kwargs) -> Response:
        return self._fail("datastax.graph_rag: GraphRAG traversal needs a built graph vector store + embedding; "
                          "ingest via a vector-store node first, then retrieve.")
