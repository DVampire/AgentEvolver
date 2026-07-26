"""PerplexityAI (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioPerplexityaiComposioPlugin(ComposioPlugin):
    name: str = "composio.perplexityai_composio"
    display_name: str = 'PerplexityAI'
    description: str = 'Execute PerplexityAI actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "perplexityai"
