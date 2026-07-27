"""Glean plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.search_api import GleanSearchApiTool


@PLUGIN.register_module(force=True)
class GleanPlugin(Plugin):
    """Glean tools."""

    tools = (GleanSearchApiTool,)

    name: str = 'glean'
    display_name: str = 'Glean'
    description: str = 'Glean tools.'
    category: str = 'data'
    type: str = 'tool'
