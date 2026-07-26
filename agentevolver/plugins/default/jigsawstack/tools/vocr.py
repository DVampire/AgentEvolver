"""VOCR — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackVocrPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.vocr"
    display_name: str = 'VOCR'
    description: str = 'Extract data from any document type in a consistent structure with fine-tuned \\\\\\n        vLLMs for the highest accuracy'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, url: str = "", prompts: list = None, api_key: str = "", **kwargs) -> Response:
        params = {"url": url, "prompts": prompts or []}
        return await self._run("vision.vocr", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
