"""Missive (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioMissiveComposioPlugin(ComposioPlugin):
    name: str = "composio.missive_composio"
    display_name: str = 'Missive'
    description: str = 'Execute Missive actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "missive"
