"""File Upload — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackFileUploadPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.file_upload"
    display_name: str = 'File Upload'
    description: str = 'Store any file seamlessly on JigsawStack File Storage and use it in your AI applications. \\\\\\n        Supports various file types including images, documents, and more.'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, file: str = "", key: str = "", overwrite: bool = False, api_key: str = "", **kwargs) -> Response:
        params = {"file": file, "key": key, "overwrite": overwrite}
        return await self._run("store.upload", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
