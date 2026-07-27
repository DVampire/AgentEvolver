"""Cloudflare plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.cloudflare import CloudflareTool


@PLUGIN.register_module(force=True)
class CloudflarePlugin(Plugin):
    """Cloudflare tools."""

    tools = (CloudflareTool,)

    name: str = 'cloudflare'
    display_name: str = 'Cloudflare'
    description: str = 'Cloudflare tools.'
    category: str = 'knowledge'
    type: str = 'embedding'
