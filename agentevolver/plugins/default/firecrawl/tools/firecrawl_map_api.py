"""Firecrawl Map API — from the Langflow `firecrawl` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class FirecrawlFirecrawlMapApiPlugin(BundlePlugin):
    name: str = "firecrawl.firecrawl_map_api"
    display_name: str = 'Firecrawl Map API'
    description: str = 'Maps a URL and returns the results.'
    kind: str = "tool"
    bundle: str = "firecrawl"
    bundle_label: str = 'Firecrawl'
    category: str = "data"
    source: str = "langflow/bundles/firecrawl"
    status: str = "complete"

    async def __call__(self, url: str = "", api_key: str = "", include_subdomains: bool = False, **kwargs) -> Response:
        key = self._secret(api_key, "FIRECRAWL_API_KEY")
        if not key:
            return self._fail("firecrawl.map: no API key (set api_key / FIRECRAWL_API_KEY).")
        if not str(url or "").strip():
            return self._fail("firecrawl.map: 'url' is required.")
        try:
            from firecrawl import Firecrawl
            app = Firecrawl(api_key=key)
            result = app.map(url, include_subdomains=include_subdomains)
            data = result.model_dump() if hasattr(result, "model_dump") else result
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"firecrawl.map: {type(exc).__name__}: {exc}")
        return self._ok("Firecrawl map completed.", result=data)
