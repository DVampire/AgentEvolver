"""TwelveLabs Text Embeddings — from the Langflow `twelvelabs` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class TwelvelabsTextEmbeddingsPlugin(BundlePlugin):
    name: str = "twelvelabs.text_embeddings"
    display_name: str = 'TwelveLabs Text Embeddings'
    description: str = 'Generate embeddings using TwelveLabs text embedding models.'
    kind: str = "tool"
    bundle: str = "twelvelabs"
    bundle_label: str = 'TwelveLabs'
    category: str = "knowledge"
    source: str = "langflow/bundles/twelvelabs"
    status: str = "complete"

    async def __call__(self, text: str = "", api_key: str = "", model_name: str = "Marengo-retrieval-2.7", **kwargs) -> Response:
        key = self._secret(api_key, "TWELVELABS_API_KEY")
        if not text or not key:
            return self._fail("twelvelabs.text_embeddings: 'text' and api_key are required.")
        try:
            from twelvelabs import TwelveLabs
            result = TwelveLabs(api_key=key).embed.create(model_name=model_name, text=text)
            segs = result.text_embedding.segments if result.text_embedding else []
            vector = [float(x) for x in segs[0].embeddings_float] if segs else []
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"twelvelabs.text_embeddings: {type(exc).__name__}: {exc}")
        return self._ok(f"Embedded text ({len(vector)} dims).", vector=vector, dims=len(vector))
