"""NSFW Detection — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackNsfwPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.nsfw"
    display_name: str = 'NSFW Detection'
    description: str = 'Detect if image/video contains NSFW content'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, url: str = "", api_key: str = "", **kwargs) -> Response:
        params = {"url": url}
        return await self._run("validate.nsfw", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
