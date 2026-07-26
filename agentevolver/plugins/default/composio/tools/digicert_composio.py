"""Digicert (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioDigicertComposioPlugin(ComposioPlugin):
    name: str = "composio.digicert_composio"
    display_name: str = 'Digicert'
    description: str = 'Execute Digicert actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "digicert"
