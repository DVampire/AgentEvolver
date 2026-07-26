"""Listennotes (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioListennotesComposioPlugin(ComposioPlugin):
    name: str = "composio.listennotes_composio"
    display_name: str = 'Listennotes'
    description: str = 'Execute Listennotes actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "listennotes"
