"""File Read — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackFileReadPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.file_read"
    display_name: str = 'File Read'
    description: str = 'Read any previously uploaded file seamlessly from \\\\\\n        JigsawStack File Storage and use it in your AI applications.'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, key: str = "", api_key: str = "", **kwargs) -> Response:
        params = {"key": key}
        return await self._run("store.get", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
