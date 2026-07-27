"""Exa Search — from the Langflow `exa` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class ExaExaSearchPlugin(BundlePlugin):
    name: str = "exa.exa_search"
    display_name: str = 'Exa Search'
    description: str = 'Exa search and contents tools for agents and MCP clients.'
    kind: str = "tool"
    bundle: str = "exa"
    bundle_label: str = 'Exa'
    category: str = "data"
    source: str = "langflow/bundles/exa"
    status: str = "complete"

    async def __call__(self, query: str = "", api_key: str = "", num_results: int = 5, **kwargs) -> Response:
        q = str(query or "").strip()
        key = self._secret(api_key, "EXA_API_KEY")
        if not q or not key:
            return self._fail("exa: 'query' and api_key (EXA_API_KEY) are required.")
        try:
            from exa_py import Exa
            resp = Exa(key).search_and_contents(q, num_results=int(num_results), text=True)
            records = [{"title": r.title, "url": r.url, "text": (getattr(r, "text", "") or "")[:2000]}
                       for r in resp.results]
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"exa: {type(exc).__name__}: {exc}")
        return self._ok(f"Exa returned {len(records)} results for '{q}'.", query=q, records=records, count=len(records))
