"""Google Search API — from the Langflow `google` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleSearchApiCorePlugin(BundlePlugin):
    name: str = "google.google_search_api_core"
    display_name: str = 'Google Search API'
    description: str = 'Call Google Search API and return results as a DataFrame.'
    kind: str = "tool"
    bundle: str = "google"
    bundle_label: str = 'Google'
    category: str = "data"
    source: str = "langflow/bundles/google"
    status: str = "complete"

    async def __call__(self, input_value: str = "", google_api_key: str = "", google_cse_id: str = "", k: int = 4, **kwargs) -> Response:
        q = str(input_value or "").strip()
        key = self._secret(google_api_key, "GOOGLE_API_KEY")
        cse = self._secret(google_cse_id, "GOOGLE_CSE_ID")
        if not q or not key or not cse:
            return self._fail("google.search: needs input_value, GOOGLE_API_KEY, GOOGLE_CSE_ID.")
        try:
            from langchain_google_community import GoogleSearchAPIWrapper
            results = GoogleSearchAPIWrapper(google_api_key=key, google_cse_id=cse).results(q, int(k))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"google.search: {type(exc).__name__}: {exc}")
        return self._ok(f"Google returned {len(results)} results.", query=q, records=results, count=len(results))
