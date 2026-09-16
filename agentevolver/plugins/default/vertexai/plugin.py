"""Vertex AI plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.embeddings import VertexaiEmbeddingsTool
from .tools.vertexai import VertexaiTool


@PLUGIN.register_module(force=True)
class VertexaiPlugin(Plugin):
    """Vertex AI tools."""

    tools = (VertexaiTool, VertexaiEmbeddingsTool,)

    name: str = 'vertexai'
    display_name: str = 'Vertex AI'
    description: str = 'Vertex AI tools.'
    category: str = 'data'
    type: str = 'model'
