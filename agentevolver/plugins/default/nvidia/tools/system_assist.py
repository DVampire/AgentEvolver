"""NVIDIA System-Assist — from the Langflow `nvidia` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class NvidiaSystemAssistPlugin(BundlePlugin):
    name: str = "nvidia.system_assist"
    display_name: str = 'NVIDIA System-Assist'
    description: str = ''
    kind: str = "tool"
    bundle: str = "nvidia"
    bundle_label: str = 'NVIDIA'
    category: str = "agent"
    source: str = "langflow/bundles/nvidia"
    status: str = "complete"

    async def __call__(self, prompt: str = "", **kwargs) -> Response:
        return self._fail("nvidia.system_assist: this is a Langflow agent-framework component (NVIDIA G-Assist / RISE local SDK); it needs the "
                          "full agent runtime and is not available as a standalone one-shot tool. "
                          "Use AgentEvolver's native agent nodes for equivalent capability.")
