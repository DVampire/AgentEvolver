"""CodeAct Agent (Smolagents)."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class CodeagentsCodeactAgentSmolagentsTool(PluginTool):
    """CodeAct Agent (Smolagents)."""

    name: str = 'codeact_agent_smolagents'
    display_name: str = 'CodeAct Agent (Smolagents)'
    description: str = 'A code-based agent using smolagents CodeAgent for complex tasks.'

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("codeagents.codeact: this is a Langflow agent-framework component (smolagents CodeAct agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
