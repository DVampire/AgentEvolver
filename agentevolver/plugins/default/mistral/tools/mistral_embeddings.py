"""MistralAI Embeddings — from the Langflow `mistral` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class MistralMistralEmbeddingsPlugin(EmbeddingPlugin):
    name: str = "mistral.mistral_embeddings"
    display_name: str = 'MistralAI Embeddings'
    description: str = 'Generate embeddings using MistralAI models.'
    kind: str = "embedding"
    bundle: str = "mistral"
    bundle_label: str = 'MistralAI'
    source: str = "langflow/bundles/mistral"
    status: str = "complete"
    key_env: str = "MISTRAL_API_KEY"
    default_base_url: str = ""

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_mistralai import MistralAIEmbeddings
        key = self._secret(cfg.get("api_key"), self.key_env)
        if not key:
            raise ValueError("no API key (set api_key or MISTRAL_API_KEY).")
        return MistralAIEmbeddings(model=cfg.get("model_name"), mistral_api_key=key)

    async def __call__(self, text: str = "", model_name: str = "mistral-embed", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
