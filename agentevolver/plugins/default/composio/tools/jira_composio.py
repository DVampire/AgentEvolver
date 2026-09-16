"""Jira (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioJiraComposioTool(ComposioPluginTool):
    """Jira."""

    name: str = 'jira_composio'
    display_name: str = 'Jira'
    description: str = 'Execute Jira actions via Composio.'
    app_name: str = 'jira'
