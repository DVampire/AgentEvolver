"""Tavily Search API — from the Langflow `tavily` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class TavilyTavilySearchPlugin(BundlePlugin):
    name: str = "tavily.tavily_search"
    display_name: str = 'Tavily Search API'
    description: str = 'Tavily Search API'
    kind: str = "tool"
    bundle: str = "tavily"
    bundle_label: str = 'Tavily'
    category: str = "data"
    source: str = "langflow/bundles/tavily"
    status: str = "complete"

    async def __call__(self, query: str = "", api_key: str = "", search_depth: str = "basic", topic: str = "general", max_results: int = 5, include_answer: bool = True, include_raw_content: bool = False, **kwargs) -> Response:
        import httpx
        query = str(query or "").strip()
        if not query:
            return self._fail("tavily.search: 'query' is required.")
        key = self._secret(api_key, "TAVILY_API_KEY")
        if not key:
            return self._fail("tavily.search: no API key (set api_key / TAVILY_API_KEY).")
        payload = {"api_key": key, "query": query, "search_depth": search_depth, "topic": topic,
                   "max_results": int(max_results), "include_answer": include_answer,
                   "include_raw_content": include_raw_content}
        try:
            with httpx.Client(timeout=90.0) as client:
                resp = client.post("https://api.tavily.com/search", json=payload,
                                   headers={"content-type": "application/json", "accept": "application/json"})
            resp.raise_for_status()
            js = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"tavily.search: {type(exc).__name__}: {exc}")
        records = [{"title": r.get("title"), "url": r.get("url"), "content": r.get("content", ""),
                    "score": r.get("score")} for r in js.get("results", [])]
        return self._ok(f"Tavily returned {len(records)} results for '{query}'.",
                        query=query, answer=js.get("answer"), records=records, count=len(records))
