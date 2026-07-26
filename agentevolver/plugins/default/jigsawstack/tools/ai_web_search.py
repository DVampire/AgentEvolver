"""AI Web Search — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackAiWebSearchPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.ai_web_search"
    display_name: str = 'AI Web Search'
    description: str = 'Effortlessly search the Web and get access to high-quality results powered with AI.'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, query: str = "", ai_overview: bool = True, safe_search: str = "moderate", api_key: str = "", **kwargs) -> Response:
        params = {"query": query, "ai_overview": ai_overview, "safe_search": safe_search}
        return await self._run("web.search", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
