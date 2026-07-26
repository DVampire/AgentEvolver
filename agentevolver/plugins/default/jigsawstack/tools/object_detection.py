"""Object Detection — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackObjectDetectionPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.object_detection"
    display_name: str = 'Object Detection'
    description: str = 'Perform object detection on images using JigsawStack'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, url: str = "", prompts: list = None, api_key: str = "", **kwargs) -> Response:
        params = {"url": url, "prompts": prompts or []}
        return await self._run("vision.object_detection", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
