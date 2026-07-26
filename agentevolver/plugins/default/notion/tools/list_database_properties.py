"""List Database Properties  — from the Langflow `notion` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.notion._base import NotionPlugin


@PLUGIN.register_module(force=True)
class NotionListDatabasePropertiesPlugin(NotionPlugin):
    name: str = "notion.list_database_properties"
    display_name: str = 'List Database Properties '
    description: str = 'Retrieve properties of a Notion database.'
    kind: str = "tool"
    bundle: str = "notion"
    bundle_label: str = "Notion"
    source: str = "langflow/bundles/notion"
    status: str = "complete"

    async def __call__(self, api_key: str = "", database_id: str = "", **kwargs) -> Response:
        err = self._need_token(api_key)
        if err:
            return err
        token = self._token(api_key)
        try:
            if not database_id:
                return self._fail("notion.list_database_properties: 'database_id' is required.")
            js = self._request("GET", f"/databases/{database_id}", token)
            props = js.get("properties", {})
            return self._ok(f"Database has {len(props)} properties.", properties=props, count=len(props))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"notion.list_database_properties: {type(exc).__name__}: {exc}")
