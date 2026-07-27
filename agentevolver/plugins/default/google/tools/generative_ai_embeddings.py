"""Google Generative AI Embeddings — from the Langflow `google` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleGenerativeAiEmbeddingsPlugin(EmbeddingPlugin):
    name: str = "google.google_generative_ai_embeddings"
    display_name: str = 'Google Generative AI Embeddings'
    description: str = 'Google Generative AI Embeddings'
    kind: str = "embedding"
    bundle: str = "google"
    bundle_label: str = 'Google'
    source: str = "langflow/bundles/google"
    status: str = "complete"

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        key = self._secret(cfg.get("api_key"), "GOOGLE_API_KEY", "GEMINI_API_KEY")
        if not key:
            raise ValueError("no API key (set api_key or GOOGLE_API_KEY).")
        return GoogleGenerativeAIEmbeddings(model=cfg.get("model_name"), google_api_key=key)

    async def __call__(self, text: str = "", model_name: str = "models/text-embedding-004", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
