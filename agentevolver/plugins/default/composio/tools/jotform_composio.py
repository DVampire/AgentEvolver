"""Jotform (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioJotformComposioTool(ComposioPluginTool):
    """Jotform."""

    name: str = 'jotform_composio'
    display_name: str = 'Jotform'
    description: str = 'Execute Jotform actions via Composio.'
    app_name: str = 'jotform'
