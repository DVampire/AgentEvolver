"""GoogleTasks (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioGoogletasksComposioPlugin(ComposioPlugin):
    name: str = "composio.googletasks_composio"
    display_name: str = 'GoogleTasks'
    description: str = 'Execute GoogleTasks actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "googletasks"
