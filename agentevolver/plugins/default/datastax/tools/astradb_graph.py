"""Astra DB Graph — from the Langflow `datastax` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DatastaxAstradbGraphPlugin(BundlePlugin):
    name: str = "datastax.astradb_graph"
    display_name: str = 'Astra DB Graph'
    description: str = 'Implementation of Graph Vector Store using Astra DB'
    kind: str = "tool"
    bundle: str = "datastax"
    bundle_label: str = 'Astra DB'
    category: str = "knowledge"
    source: str = "langflow/bundles/datastax"
    status: str = "complete"

    async def __call__(self, collection_name: str = "", token: str = "", api_endpoint: str = "", **kwargs) -> Response:
        return self._fail("datastax.graph: the Astra DB graph vector store needs an embedding model and a "
                          "reachable collection; wire it via datastax.astradb_vectorstore for standalone use.")
