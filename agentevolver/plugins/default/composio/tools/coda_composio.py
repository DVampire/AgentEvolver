"""Coda (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioCodaComposioPlugin(ComposioPlugin):
    name: str = "composio.coda_composio"
    display_name: str = 'Coda'
    description: str = 'Execute Coda actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "coda"
