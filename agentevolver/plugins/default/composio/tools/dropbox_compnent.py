"""Dropbox (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioDropboxCompnentPlugin(ComposioPlugin):
    name: str = "composio.dropbox_compnent"
    display_name: str = 'Dropbox'
    description: str = 'Execute Dropbox actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "dropbox"
