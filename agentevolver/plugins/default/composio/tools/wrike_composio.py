"""Wrike (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioWrikeComposioPlugin(ComposioPlugin):
    name: str = "composio.wrike_composio"
    display_name: str = 'Wrike'
    description: str = 'Execute Wrike actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "wrike"
