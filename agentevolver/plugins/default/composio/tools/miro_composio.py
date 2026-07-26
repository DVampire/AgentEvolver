"""Miro (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioMiroComposioPlugin(ComposioPlugin):
    name: str = "composio.miro_composio"
    display_name: str = 'Miro'
    description: str = 'Execute Miro actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "miro"
