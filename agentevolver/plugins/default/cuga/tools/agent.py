"""Cuga — from the Langflow `cuga` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class CugaCugaAgentPlugin(BundlePlugin):
    name: str = "cuga.cuga_agent"
    display_name: str = 'Cuga'
    description: str = 'Define the Cuga agent'
    kind: str = "tool"
    bundle: str = "cuga"
    bundle_label: str = 'CUGA'
    category: str = "agent"
    source: str = "langflow/bundles/cuga"
    status: str = "complete"

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("cuga.agent: this is a Langflow agent-framework component (CUGA generalist agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
