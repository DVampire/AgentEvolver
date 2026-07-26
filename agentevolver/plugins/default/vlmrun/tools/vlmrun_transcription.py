"""VLM Run Transcription — from the Langflow `vlmrun` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class VlmrunVlmrunTranscriptionPlugin(BundlePlugin):
    name: str = "vlmrun.vlmrun_transcription"
    display_name: str = 'VLM Run Transcription'
    description: str = 'Extract structured data from audio and video using [VLM Run AI](https://app.vlm.run)'
    kind: str = "tool"
    bundle: str = "vlmrun"
    bundle_label: str = 'VLM Run'
    category: str = "data"
    source: str = "langflow/bundles/vlmrun"
    status: str = "complete"

    async def __call__(self, url: str = "", api_key: str = "", domain: str = "document.markdown", **kwargs) -> Response:
        key = self._secret(api_key, "VLMRUN_API_KEY")
        if not url or not key:
            return self._fail("vlmrun: 'url' and api_key (VLMRUN_API_KEY) are required.")
        try:
            from vlmrun.client import VLMRun
            client = VLMRun(api_key=key)
            result = client.document.generate(url=url, domain=domain)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"vlmrun: {type(exc).__name__}: {exc}")
        return self._ok("VLM Run transcription completed.", result=str(result))
