"""Astra DB Tool — from the Langflow `datastax` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DatastaxAstradbToolPlugin(BundlePlugin):
    name: str = "datastax.astradb_tool"
    display_name: str = 'Astra DB Tool'
    description: str = 'Tool to run hybrid vector and metadata search on DataStax Astra DB Collection'
    kind: str = "tool"
    bundle: str = "datastax"
    bundle_label: str = 'Astra DB'
    category: str = "tool"
    source: str = "langflow/bundles/datastax"
    status: str = "complete"

    async def __call__(self, tool_name: str = "", token: str = "", api_endpoint: str = "", **kwargs) -> Response:
        return self._fail("datastax.tool: this exposes an Astra DB collection AS an agent tool; use it as a "
                          "mounted capability on an agent node rather than a one-shot call.")
