"""Combinatorial Reasoner — from the Langflow `icosacomputing` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class IcosacomputingCombinatorialReasonerPlugin(BundlePlugin):
    name: str = "icosacomputing.combinatorial_reasoner"
    display_name: str = 'Combinatorial Reasoner'
    description: str = 'Uses Combinatorial Optimization to construct an optimal prompt with embedded reasons. Sign up here:\\\\nhttps://forms.gle/oWNv2NKjBNaqqvCx6'
    kind: str = "tool"
    bundle: str = "icosacomputing"
    bundle_label: str = 'Icosa'
    category: str = "agent"
    source: str = "langflow/bundles/icosacomputing"
    status: str = "complete"

    async def __call__(self, prompt: str = "", api_key: str = "", username: str = "", **kwargs) -> Response:
        import httpx
        key = self._secret(api_key, "ICOSA_API_KEY")
        if not prompt or not key:
            return self._fail("icosacomputing: 'prompt' and api_key are required.")
        try:
            resp = httpx.post("https://cr-api.icosacomputing.com/cr/langflow",
                              headers={"aandi-api-key": key},
                              json={"prompt": prompt, "apiKey": key, "username": username}, timeout=90.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"icosacomputing: {type(exc).__name__}: {exc}")
        return self._ok("Combinatorial reasoning completed.",
                        prompt=data.get("finalPrompt"), reasons=data.get("reason"))
