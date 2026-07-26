"""Glean Search API — from the Langflow `glean` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GleanGleanSearchApiPlugin(BundlePlugin):
    name: str = "glean.glean_search_api"
    display_name: str = 'Glean Search API'
    description: str = 'Search using Glean'
    kind: str = "tool"
    bundle: str = "glean"
    bundle_label: str = 'Glean'
    category: str = "data"
    source: str = "langflow/bundles/glean"
    status: str = "complete"

    async def __call__(self, query: str = "", glean_api_url: str = "", glean_access_token: str = "", page_size: int = 10, **kwargs) -> Response:
        import httpx
        query = str(query or "").strip()
        api_url = str(glean_api_url or "").strip()
        token = self._secret(glean_access_token, "GLEAN_ACCESS_TOKEN")
        if not query or not api_url or not token:
            return self._fail("glean.search: 'query', 'glean_api_url' and access token are required.")
        if not api_url.endswith("/"):
            api_url += "/"
        try:
            resp = httpx.post(
                api_url + "search",
                headers={"Authorization": f"Bearer {token}",
                         "X-Scio-ActAs": "agentevolver@bundle"},
                json={"query": query, "pageSize": int(page_size)}, timeout=60.0)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"glean.search: {type(exc).__name__}: {exc}")
        return self._ok(f"Glean returned {len(results)} results for '{query}'.",
                        query=query, records=results, count=len(results))
