"""MariTalk plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.maritalk import MaritalkTool


@PLUGIN.register_module(force=True)
class MaritalkPlugin(Plugin):
    """MariTalk tools."""

    tools = (MaritalkTool,)

    name: str = 'maritalk'
    display_name: str = 'MariTalk'
    description: str = 'MariTalk tools.'
    category: str = 'data'
    type: str = 'model'
