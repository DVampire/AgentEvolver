"""FAISS plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.faiss import FaissTool


@PLUGIN.register_module(force=True)
class FaissPlugin(Plugin):
    """FAISS tools."""

    tools = (FaissTool,)

    name: str = 'faiss'
    display_name: str = 'FAISS'
    description: str = 'FAISS tools.'
    category: str = 'data'
    type: str = 'vectorstore'
