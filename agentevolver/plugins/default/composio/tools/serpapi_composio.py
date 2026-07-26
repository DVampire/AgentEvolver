"""SerpAPI (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioSerpapiComposioPlugin(ComposioPlugin):
    name: str = "composio.serpapi_composio"
    display_name: str = 'SerpAPI'
    description: str = 'Execute SerpAPI actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "serpapi"
