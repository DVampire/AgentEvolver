"""Calendly (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioCalendlyComposioPlugin(ComposioPlugin):
    name: str = "composio.calendly_composio"
    display_name: str = 'Calendly'
    description: str = 'Execute Calendly actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "calendly"
