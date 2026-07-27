"""Figma (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioFigmaComposioTool(ComposioPluginTool):
    """Figma."""

    name: str = 'figma_composio'
    display_name: str = 'Figma'
    description: str = 'Execute Figma actions via Composio.'
    app_name: str = 'figma'
