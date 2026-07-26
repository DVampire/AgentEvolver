"""Contentful (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioContentfulComposioPlugin(ComposioPlugin):
    name: str = "composio.contentful_composio"
    display_name: str = 'Contentful'
    description: str = 'Execute Contentful actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "contentful"
