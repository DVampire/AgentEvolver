"""Cuga."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class CugaAgentTool(PluginTool):
    """Cuga."""

    name: str = 'cuga_agent'
    display_name: str = 'Cuga'
    description: str = 'Define the Cuga agent'

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("cuga.agent: this is a Langflow agent-framework component (CUGA generalist agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
