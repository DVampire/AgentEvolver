"""Place Call — from the Langflow `olivya` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class OlivyaOlivyaPlugin(BundlePlugin):
    name: str = "olivya.olivya"
    display_name: str = 'Place Call'
    description: str = 'A component to create an outbound call request from Olivya'
    kind: str = "tool"
    bundle: str = "olivya"
    bundle_label: str = 'Olivya'
    category: str = "data"
    source: str = "langflow/bundles/olivya"
    status: str = "complete"

    async def __call__(self, from_number: str = "", to_number: str = "", first_message: str = "", system_prompt: str = "", api_key: str = "", **kwargs) -> Response:
        import httpx
        key = self._secret(api_key, "OLIVYA_API_KEY")
        if not from_number or not to_number or not key:
            return self._fail("olivya: 'from_number', 'to_number' and api_key (OLIVYA_API_KEY) are required.")
        payload = {"variables": {"first_message": first_message or None, "system_prompt": system_prompt or None},
                   "from_number": from_number.strip(), "to_number": to_number.strip()}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post("https://phone.olivya.io/create_zap_call",
                                         headers={"Authorization": key, "Content-Type": "application/json"},
                                         json=payload, timeout=30.0)
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"olivya: {type(exc).__name__}: {exc}")
        return self._ok("Olivya call created.", result=result)
