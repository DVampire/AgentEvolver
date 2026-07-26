"""Google Classroom (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioGoogleclassroomComposioPlugin(ComposioPlugin):
    name: str = "composio.googleclassroom_composio"
    display_name: str = 'Google Classroom'
    description: str = 'Execute Google Classroom actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "GOOGLE_CLASSROOM"
