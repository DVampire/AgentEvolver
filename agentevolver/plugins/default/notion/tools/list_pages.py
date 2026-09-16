"""List Pages ."""

from typing import Any, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.default.notion._base import NotionToolBase


class NotionListPagesTool(NotionToolBase):
    """List Pages ."""

    name: str = 'list_pages'
    display_name: str = 'List Pages '
    description: str = 'List Pages '

    output = {'records': 'list', 'count': 'any'}

    def _render(self, data):
        return f"Notion returned {data['count']} pages."

    async def __call__(self, api_key: str = "", database_id: str = "", query_json: str = "", **kwargs) -> Response:
        err = self._need_token(api_key)
        if err:
            return err
        token = self._token(api_key)
        try:
            import json as _json
            if not database_id:
                return self._fail("notion.list_pages: 'database_id' is required.")
            payload = _json.loads(query_json) if query_json else {}
            js = self._request("POST", f"/databases/{database_id}/query", token, json=payload)
            results = js.get("results", [])
            return self._ok(records=results, count=len(results))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"notion.list_pages: {type(exc).__name__}: {exc}")
