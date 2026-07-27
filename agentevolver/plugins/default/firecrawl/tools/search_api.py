"""Firecrawl Search API — from the Langflow `firecrawl` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class FirecrawlFirecrawlSearchApiPlugin(BundlePlugin):
    name: str = "firecrawl.firecrawl_search_api"
    display_name: str = 'Firecrawl Search API'
    description: str = 'Searches the web and returns the results.'
    kind: str = "tool"
    bundle: str = "firecrawl"
    bundle_label: str = 'Firecrawl'
    category: str = "data"
    source: str = "langflow/bundles/firecrawl"
    status: str = "complete"

    async def __call__(self, query: str = "", api_key: str = "", limit: int = 5, **kwargs) -> Response:
        key = self._secret(api_key, "FIRECRAWL_API_KEY")
        if not key:
            return self._fail("firecrawl.search: no API key (set api_key / FIRECRAWL_API_KEY).")
        if not str(query or "").strip():
            return self._fail("firecrawl.search: 'query' is required.")
        try:
            from firecrawl import Firecrawl
            app = Firecrawl(api_key=key)
            result = app.search(query, limit=int(limit))
            data = result.model_dump() if hasattr(result, "model_dump") else result
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"firecrawl.search: {type(exc).__name__}: {exc}")
        return self._ok("Firecrawl search completed.", result=data)
