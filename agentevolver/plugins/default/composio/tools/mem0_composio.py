"""Mem0 (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioMem0ComposioPlugin(ComposioPlugin):
    name: str = "composio.mem0_composio"
    display_name: str = 'Mem0'
    description: str = 'Execute Mem0 actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "mem0"
