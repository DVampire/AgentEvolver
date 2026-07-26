"""Todoist (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioTodoistComposioPlugin(ComposioPlugin):
    name: str = "composio.todoist_composio"
    display_name: str = 'Todoist'
    description: str = 'Execute Todoist actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "todoist"
