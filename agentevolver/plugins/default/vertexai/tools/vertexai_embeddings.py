"""Vertex AI Embeddings — from the Langflow `vertexai` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class VertexaiVertexaiEmbeddingsPlugin(EmbeddingPlugin):
    name: str = "vertexai.vertexai_embeddings"
    display_name: str = 'Vertex AI Embeddings'
    description: str = 'Generate embeddings using Google Cloud Vertex AI models.'
    kind: str = "embedding"
    bundle: str = "vertexai"
    bundle_label: str = 'Vertex AI'
    source: str = "langflow/bundles/vertexai"
    status: str = "complete"

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_google_vertexai import VertexAIEmbeddings
        return VertexAIEmbeddings(model_name=cfg.get("model_name"))

    async def __call__(self, text: str = "", model_name: str = "text-embedding-004", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
