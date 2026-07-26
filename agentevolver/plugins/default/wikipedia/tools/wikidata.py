"""Wikidata — from the Langflow `wikipedia` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class WikipediaWikidataPlugin(BundlePlugin):
    name: str = "wikipedia.wikidata"
    display_name: str = 'Wikidata'
    description: str = 'Performs a search using the Wikidata API.'
    kind: str = "tool"
    bundle: str = "wikipedia"
    bundle_label: str = 'Wikipedia'
    category: str = "data"
    source: str = "langflow/bundles/wikipedia"
    status: str = "complete"

    async def __call__(self, query: str = "", **kwargs) -> Response:
        import httpx
        query = str(query or "").strip()
        if not query:
            return self._fail("wikipedia.wikidata: 'query' is required.")
        try:
            resp = httpx.get("https://www.wikidata.org/w/api.php",
                             params={"action": "wbsearchentities", "format": "json", "search": query, "language": "en"},
                             headers={"User-Agent": "AgentEvolver/1.0 (bundle plugin; +https://agentevolver)"},
                             timeout=30.0)
            resp.raise_for_status()
            results = resp.json().get("search", [])
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"wikipedia.wikidata: {type(exc).__name__}: {exc}")
        records = [{"label": r.get("label"), "id": r.get("id"), "url": r.get("url"),
                    "description": r.get("description", ""), "concepturi": r.get("concepturi")} for r in results]
        return self._ok(f"Wikidata returned {len(records)} entities for '{query}'.",
                        query=query, records=records, count=len(records))
