"""Fireflies (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioFirefliesComposioPlugin(ComposioPlugin):
    name: str = "composio.fireflies_composio"
    display_name: str = 'Fireflies'
    description: str = 'Execute Fireflies actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "fireflies"
