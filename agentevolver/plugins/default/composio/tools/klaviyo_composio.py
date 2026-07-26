"""Klaviyo (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioKlaviyoComposioPlugin(ComposioPlugin):
    name: str = "composio.klaviyo_composio"
    display_name: str = 'Klaviyo'
    description: str = 'Execute Klaviyo actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "klaviyo"
