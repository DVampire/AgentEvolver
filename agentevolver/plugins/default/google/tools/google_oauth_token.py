"""Google OAuth Token — from the Langflow `google` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleOauthTokenPlugin(BundlePlugin):
    name: str = "google.google_oauth_token"
    display_name: str = 'Google OAuth Token'
    description: str = 'Generates a JSON string with your Google OAuth token.'
    kind: str = "tool"
    bundle: str = "google"
    bundle_label: str = 'Google'
    category: str = "data"
    source: str = "langflow/bundles/google"
    status: str = "complete"

    async def __call__(self, credentials_json: str = "", scopes: str = "", **kwargs) -> Response:
        return self._fail("google.oauth_token: performs an interactive Google OAuth flow; supply a "
                          "service-account or pre-authorized token JSON (credentials_json) instead.")
