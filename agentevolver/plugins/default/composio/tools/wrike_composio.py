"""Wrike (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioWrikeComposioTool(ComposioPluginTool):
    """Wrike."""

    name: str = 'wrike_composio'
    display_name: str = 'Wrike'
    description: str = 'Execute Wrike actions via Composio.'
    app_name: str = 'wrike'
