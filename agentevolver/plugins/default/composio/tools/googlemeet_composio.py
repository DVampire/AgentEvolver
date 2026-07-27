"""GoogleMeet (Composio)."""

from agentevolver.plugins.types import ComposioPluginTool


class ComposioGooglemeetComposioTool(ComposioPluginTool):
    """GoogleMeet."""

    name: str = 'googlemeet_composio'
    display_name: str = 'GoogleMeet'
    description: str = 'Execute GoogleMeet actions via Composio.'
    app_name: str = 'googlemeet'
