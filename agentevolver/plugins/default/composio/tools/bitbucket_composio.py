"""Bitbucket (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioBitbucketComposioPlugin(ComposioPlugin):
    name: str = "composio.bitbucket_composio"
    display_name: str = 'Bitbucket'
    description: str = 'Execute Bitbucket actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "bitbucket"
