"""Extract Web Data."""

from typing import Any, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class AgentqlApiTool(PluginTool):
    """Extract Web Data."""

    name: str = 'agentql_api'
    display_name: str = 'Extract Web Data'
    description: str = 'Extracts structured data from a web page using an AgentQL query or a Natural Language description.'

    output = {'result': 'object'}

    def _render(self, data):
        return 'AgentQL query completed.'

    async def __call__(self, url: str = "", query: str = "", prompt: str = "", api_key: str = "", mode: str = "fast", **kwargs) -> Response:
        import httpx
        key = self._secret(api_key, "AGENTQL_API_KEY")
        if not url or (not query and not prompt) or not key:
            return self._fail("agentql: needs 'url', a 'query' or 'prompt', and an API key (AGENTQL_API_KEY).")
        payload = {"url": url, "query": query or None, "prompt": prompt or None, "params": {"mode": mode}}
        try:
            resp = httpx.post("https://api.agentql.com/v1/query-data",
                              headers={"X-API-Key": key, "Content-Type": "application/json"},
                              json=payload, timeout=90.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"agentql: {type(exc).__name__}: {exc}")
        return self._ok(result=data.get('data', data))
