"""Sentiment Analysis — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackSentimentPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.sentiment"
    display_name: str = 'Sentiment Analysis'
    description: str = 'Analyze sentiment of text using JigsawStack AI'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, text: str = "", api_key: str = "", **kwargs) -> Response:
        params = {"text": text}
        return await self._run("sentiment", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
