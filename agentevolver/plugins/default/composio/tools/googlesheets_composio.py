"""GoogleSheets (Composio) — from the Langflow `composio` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.plugins.types import ComposioPlugin


@PLUGIN.register_module(force=True)
class ComposioGooglesheetsComposioPlugin(ComposioPlugin):
    name: str = "composio.googlesheets_composio"
    display_name: str = 'GoogleSheets'
    description: str = 'Execute GoogleSheets actions via Composio.'
    kind: str = "tool"
    bundle: str = "composio"
    bundle_label: str = "Composio"
    source: str = "langflow/bundles/composio"
    status: str = "complete"
    app_name: str = "googlesheets"
