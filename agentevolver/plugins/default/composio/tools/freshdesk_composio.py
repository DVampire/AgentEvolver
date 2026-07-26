"""Freshdesk (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioFreshdeskComposioPlugin(ComposioPlugin):
    name: str = "composio.freshdesk_composio"
    display_name: str = 'Freshdesk'
    description: str = 'Execute Freshdesk actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "freshdesk"
