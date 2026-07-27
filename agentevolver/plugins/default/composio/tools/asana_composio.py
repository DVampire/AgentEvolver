"""Asana (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioAsanaComposioTool(ComposioPluginTool):
    """Asana."""

    name: str = 'asana_composio'
    display_name: str = 'Asana'
    description: str = 'Execute Asana actions via Composio.'
    app_name: str = 'asana'
