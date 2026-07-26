"""Figma (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioFigmaComposioPlugin(ComposioPlugin):
    name: str = "composio.figma_composio"
    display_name: str = 'Figma'
    description: str = 'Execute Figma actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "figma"
