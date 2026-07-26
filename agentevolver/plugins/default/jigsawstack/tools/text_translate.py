"""Text Translate — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackTextTranslatePlugin(JigsawStackPlugin):
    name: str = "jigsawstack.text_translate"
    display_name: str = 'Text Translate'
    description: str = 'Translate text from one language to another with support for multiple text formats.'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, text: str = "", target_language: str = "en", api_key: str = "", **kwargs) -> Response:
        params = {"text": text, "target_language": target_language}
        return await self._run("translate.text", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
