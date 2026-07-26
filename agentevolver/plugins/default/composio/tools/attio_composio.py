"""Attio (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioAttioComposioPlugin(ComposioPlugin):
    name: str = "composio.attio_composio"
    display_name: str = 'Attio'
    description: str = 'Execute Attio actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "attio"
