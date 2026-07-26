"""EmpirioLabs AI Image Generation — from the Langflow `empiriolabs` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class EmpiriolabsEmpiriolabsImageGenerationPlugin(BundlePlugin):
    name: str = "empiriolabs.empiriolabs_image_generation"
    display_name: str = 'EmpirioLabs AI Image Generation'
    description: str = 'Generate an image from a text prompt using EmpirioLabs AI image models such as Seedream, \\\\\\n        Qwen-Image, FLUX, Nova Canvas, and HunyuanImage.'
    kind: str = "tool"
    bundle: str = "empiriolabs"
    bundle_label: str = 'EmpirioLabs'
    category: str = "data"
    source: str = "langflow/bundles/empiriolabs"
    status: str = "complete"

    async def __call__(self, prompt: str = "", api_key: str = "", **kwargs) -> Response:
        import httpx
        key = self._secret(api_key, "EMPIRIOLABS_API_KEY")
        if not prompt or not key:
            return self._fail("empiriolabs.image: 'prompt' and api_key are required.")
        try:
            resp = httpx.post("https://api.empiriolabs.ai/v1/images/generations",
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"prompt": prompt}, timeout=120.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"empiriolabs.image: {type(exc).__name__}: {exc}")
        return self._ok("Image generated.", result=data)
