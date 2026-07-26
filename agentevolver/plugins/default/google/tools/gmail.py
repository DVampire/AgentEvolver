"""Gmail Loader — from the Langflow `google` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GoogleGmailPlugin(BundlePlugin):
    name: str = "google.gmail"
    display_name: str = 'Gmail Loader'
    description: str = 'Loads emails from Gmail using provided credentials.'
    kind: str = "tool"
    bundle: str = "google"
    bundle_label: str = 'Google'
    category: str = "data"
    source: str = "langflow/bundles/google"
    status: str = "complete"

    async def __call__(self, query: str = "", credentials_json: str = "", **kwargs) -> Response:
        try:
            from langchain_google_community import GmailToolkit
            from langchain_google_community.gmail.utils import build_resource_service
            toolkit = GmailToolkit(api_resource=build_resource_service())
            search = next(t for t in toolkit.get_tools() if "search" in t.name.lower())
            result = search.invoke({"query": query or "in:inbox"})
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"google.gmail: {type(exc).__name__}: {exc}")
        return self._ok("Gmail search completed.", result=result)
