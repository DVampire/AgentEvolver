"""Apify Actors — from the Langflow `apify` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class ApifyApifyActorPlugin(BundlePlugin):
    name: str = "apify.apify_actor"
    display_name: str = 'Apify Actors'
    description: str = 'Apify Actors'
    kind: str = "tool"
    bundle: str = "apify"
    bundle_label: str = 'Apify'
    category: str = "data"
    source: str = "langflow/bundles/apify"
    status: str = "complete"

    async def __call__(self, actor_id: str = "", run_input: Optional[dict] = None, api_key: str = "", **kwargs) -> Response:
        key = self._secret(api_key, "APIFY_API_TOKEN", "APIFY_TOKEN")
        if not actor_id or not key:
            return self._fail("apify: 'actor_id' and api_key (APIFY_API_TOKEN) are required.")
        try:
            from apify_client import ApifyClient
            client = ApifyClient(key)
            run = client.actor(actor_id).call(run_input=run_input or {})
            items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"apify: {type(exc).__name__}: {exc}")
        return self._ok(f"Apify actor '{actor_id}' produced {len(items)} items.", records=items, count=len(items))
