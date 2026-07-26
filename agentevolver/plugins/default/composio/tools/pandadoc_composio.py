"""Pandadoc (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioPandadocComposioPlugin(ComposioPlugin):
    name: str = "composio.pandadoc_composio"
    display_name: str = 'Pandadoc'
    description: str = 'Execute Pandadoc actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "pandadoc"
