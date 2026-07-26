"""Serp Search API — from the Langflow `serpapi` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class SerpapiSerpPlugin(BundlePlugin):
    name: str = "serpapi.serp"
    display_name: str = 'Serp Search API'
    description: str = 'Call Serp Search API with result limiting'
    kind: str = "tool"
    bundle: str = "serpapi"
    bundle_label: str = 'SerpAPI'
    category: str = "data"
    source: str = "langflow/bundles/serpapi"
    status: str = "complete"

    async def __call__(self, input_value: str = "", api_key: str = "", max_results: int = 5, max_snippet_length: int = 100, **kwargs) -> Response:
        query = str(input_value or "").strip()
        if not query:
            return self._fail("serpapi.serp: 'input_value' is required.")
        key = self._secret(api_key, "SERPAPI_API_KEY", "SERP_API_KEY")
        if not key:
            return self._fail("serpapi.serp: no API key (set api_key / SERPAPI_API_KEY).")
        try:
            from langchain_community.utilities.serpapi import SerpAPIWrapper
            wrapper = SerpAPIWrapper(serpapi_api_key=key)
            full = wrapper.results(query)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"serpapi.serp: {type(exc).__name__}: {exc}")
        organic = (full or {}).get("organic_results", [])[: int(max_results)]
        records = [{"title": r.get("title", "")[: int(max_snippet_length)], "link": r.get("link", ""),
                    "snippet": r.get("snippet", "")[: int(max_snippet_length)]} for r in organic]
        return self._ok(f"SerpAPI returned {len(records)} results for '{query}'.",
                        query=query, records=records, count=len(records))
