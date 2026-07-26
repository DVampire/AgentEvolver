"""Exa (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioExaComposioPlugin(ComposioPlugin):
    name: str = "composio.exa_composio"
    display_name: str = 'Exa'
    description: str = 'Execute Exa actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "exa"
