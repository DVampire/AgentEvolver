"""ALTK Agent."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class AltkAgentTool(PluginTool):
    """ALTK Agent."""

    name: str = 'altk_agent'
    display_name: str = 'ALTK Agent'
    description: str = 'Advanced agent with both pre-tool validation and post-tool processing capabilities.'

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("altk.agent: this is a Langflow agent-framework component (IBM ALTK agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
