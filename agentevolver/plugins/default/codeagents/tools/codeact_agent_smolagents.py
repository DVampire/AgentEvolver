"""CodeAct Agent (Smolagents) — from the Langflow `codeagents` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class CodeagentsCodeactAgentSmolagentsPlugin(BundlePlugin):
    name: str = "codeagents.codeact_agent_smolagents"
    display_name: str = 'CodeAct Agent (Smolagents)'
    description: str = 'A code-based agent using smolagents CodeAgent for complex tasks.'
    kind: str = "tool"
    bundle: str = "codeagents"
    bundle_label: str = 'Code Agents'
    category: str = "agent"
    source: str = "langflow/bundles/codeagents"
    status: str = "complete"

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("codeagents.codeact: this is a Langflow agent-framework component (smolagents CodeAct agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
