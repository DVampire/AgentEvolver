"""Tavily Extract API — from the Langflow `tavily` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class TavilyTavilyExtractPlugin(BundlePlugin):
    name: str = "tavily.tavily_extract"
    display_name: str = 'Tavily Extract API'
    description: str = 'Tavily Extract API'
    kind: str = "tool"
    bundle: str = "tavily"
    bundle_label: str = 'Tavily'
    category: str = "data"
    source: str = "langflow/bundles/tavily"
    status: str = "complete"

    async def __call__(self, urls: str = "", api_key: str = "", extract_depth: str = "basic", include_images: bool = False, **kwargs) -> Response:
        import httpx
        url_list = [u.strip() for u in str(urls or "").split(",") if u.strip()]
        if not url_list:
            return self._fail("tavily.extract: 'urls' (comma-separated) is required.")
        key = self._secret(api_key, "TAVILY_API_KEY")
        if not key:
            return self._fail("tavily.extract: no API key (set api_key / TAVILY_API_KEY).")
        try:
            with httpx.Client(timeout=90.0) as client:
                resp = client.post("https://api.tavily.com/extract",
                                   json={"urls": url_list, "extract_depth": extract_depth, "include_images": include_images},
                                   headers={"content-type": "application/json", "accept": "application/json",
                                            "Authorization": f"Bearer {key}"})
            resp.raise_for_status()
            js = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"tavily.extract: {type(exc).__name__}: {exc}")
        records = [{"url": r.get("url"), "raw_content": r.get("raw_content", ""), "images": r.get("images", [])}
                   for r in js.get("results", [])]
        return self._ok(f"Extracted content from {len(records)} URL(s).",
                        records=records, failed=js.get("failed_results", []), count=len(records))
