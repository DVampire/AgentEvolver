"""Page Content Viewer  — from the Langflow `notion` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.notion._base import NotionPlugin


@PLUGIN.register_module(force=True)
class NotionPageContentViewerPlugin(NotionPlugin):
    name: str = "notion.page_content_viewer"
    display_name: str = 'Page Content Viewer '
    description: str = 'Retrieve the content of a Notion page as plain text.'
    kind: str = "tool"
    bundle: str = "notion"
    bundle_label: str = "Notion"
    source: str = "langflow/bundles/notion"
    status: str = "complete"

    async def __call__(self, api_key: str = "", page_id: str = "", **kwargs) -> Response:
        err = self._need_token(api_key)
        if err:
            return err
        token = self._token(api_key)
        try:
            if not page_id:
                return self._fail("notion.page_content_viewer: 'page_id' is required.")
            js = self._request("GET", f"/blocks/{page_id}/children?page_size=100", token)
            blocks = js.get("results", [])
            return self._ok(f"Page {page_id} has {len(blocks)} blocks.", records=blocks, count=len(blocks))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"notion.page_content_viewer: {type(exc).__name__}: {exc}")
