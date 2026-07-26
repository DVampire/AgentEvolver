"""Composio API — generic Composio action executor (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioComposioApiPlugin(ComposioPlugin):
    name: str = "composio.composio_api"
    display_name: str = "Composio API"
    description: str = "Execute any Composio action across connected apps."
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "composio"
