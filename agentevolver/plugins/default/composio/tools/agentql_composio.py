"""AgentQL (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioAgentqlComposioPlugin(ComposioPlugin):
    name: str = "composio.agentql_composio"
    display_name: str = 'AgentQL'
    description: str = 'Execute AgentQL actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "agentql"
