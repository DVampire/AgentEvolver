"""Google Drive Search — from the Langflow `google` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleDriveSearchPlugin(BundlePlugin):
    name: str = "google.google_drive_search"
    display_name: str = 'Google Drive Search'
    description: str = 'Searches Google Drive files using provided credentials and query parameters.'
    kind: str = "tool"
    bundle: str = "google"
    bundle_label: str = 'Google'
    category: str = "data"
    source: str = "langflow/bundles/google"
    status: str = "complete"

    async def __call__(self, query: str = "", credentials_json: str = "", **kwargs) -> Response:
        return self._fail("google.drive_search: requires Google OAuth credentials (credentials_json) "
                          "and the Drive API; configure a service account or OAuth token to use.")
