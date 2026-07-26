"""OpenDsStar Agent — from the Langflow `codeagents` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class CodeagentsOpenDsStarAgentPlugin(BundlePlugin):
    name: str = "codeagents.open_ds_star_agent"
    display_name: str = 'OpenDsStar Agent'
    description: str = 'A tool-based DS-Star agent using LangGraph for complex data science tasks.'
    kind: str = "tool"
    bundle: str = "codeagents"
    bundle_label: str = 'Code Agents'
    category: str = "agent"
    source: str = "langflow/bundles/codeagents"
    status: str = "complete"

    async def __call__(self, input_value: str = "", **kwargs) -> Response:
        return self._fail("codeagents.ds_star: this is a Langflow agent-framework component (DS-STAR data-science agent); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
