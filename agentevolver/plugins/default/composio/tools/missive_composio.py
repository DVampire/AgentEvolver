"""Missive (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioMissiveComposioTool(ComposioPluginTool):
    """Missive."""

    name: str = 'missive_composio'
    display_name: str = 'Missive'
    description: str = 'Execute Missive actions via Composio.'
    app_name: str = 'missive'
