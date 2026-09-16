"""Exa plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.search import ExaSearchTool


@PLUGIN.register_module(force=True)
class ExaPlugin(Plugin):
    """Exa tools."""

    tools = (ExaSearchTool,)

    name: str = 'exa'
    display_name: str = 'Exa'
    description: str = 'Exa tools.'
    category: str = 'data'
    type: str = 'tool'
