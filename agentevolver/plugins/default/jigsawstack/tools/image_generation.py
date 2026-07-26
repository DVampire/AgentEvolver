"""Image Generation — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackImageGenerationPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.image_generation"
    display_name: str = 'Image Generation'
    description: str = 'Generate an image based on the given text by employing AI models like Flux, \\\\\\n        Stable Diffusion, and other top models.'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, prompt: str = "", aspect_ratio: str = "1:1", api_key: str = "", **kwargs) -> Response:
        params = {"prompt": prompt, "aspect_ratio": aspect_ratio}
        return await self._run("image_generation", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
