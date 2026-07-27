"""PGVector plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.pgvector import PgvectorTool


@PLUGIN.register_module(force=True)
class PgvectorPlugin(Plugin):
    """PGVector tools."""

    tools = (PgvectorTool,)

    name: str = 'pgvector'
    display_name: str = 'PGVector'
    description: str = 'PGVector tools.'
    category: str = 'data'
    type: str = 'vectorstore'
