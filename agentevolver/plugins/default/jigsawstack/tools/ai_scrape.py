"""AI Scraper — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackAiScrapePlugin(JigsawStackPlugin):
    name: str = "jigsawstack.ai_scrape"
    display_name: str = 'AI Scraper'
    description: str = 'Scrape any website instantly and get consistent structured data \\\\\\n        in seconds without writing any css selector code'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, url: str = "", element_prompts: list = None, api_key: str = "", **kwargs) -> Response:
        params = {"url": url, "element_prompts": element_prompts or []}
        return await self._run("web.ai_scrape", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
