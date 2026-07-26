"""GoogleBigQuery (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioGooglebigqueryComposioPlugin(ComposioPlugin):
    name: str = "composio.googlebigquery_composio"
    display_name: str = 'GoogleBigQuery'
    description: str = 'Execute GoogleBigQuery actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "googlebigquery"
