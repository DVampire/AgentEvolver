"""Hugging Face Embeddings Inference — from the Langflow `huggingface` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class HuggingfaceHuggingfaceInferenceApiPlugin(EmbeddingPlugin):
    name: str = "huggingface.huggingface_inference_api"
    display_name: str = 'Hugging Face Embeddings Inference'
    description: str = 'Generate embeddings using Hugging Face Text Embeddings Inference (TEI)'
    kind: str = "embedding"
    bundle: str = "huggingface"
    bundle_label: str = 'Hugging Face'
    source: str = "langflow/bundles/huggingface"
    status: str = "complete"
    key_env: str = "HUGGINGFACEHUB_API_TOKEN"
    default_base_url: str = ""

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_community.embeddings import HuggingFaceInferenceAPIEmbeddings
        key = self._secret(cfg.get("api_key"), self.key_env, "HF_TOKEN")
        if not key:
            raise ValueError("no API key (set api_key or HUGGINGFACEHUB_API_TOKEN).")
        return HuggingFaceInferenceAPIEmbeddings(api_key=key, model_name=cfg.get("model_name"))

    async def __call__(self, text: str = "", model_name: str = "sentence-transformers/all-MiniLM-L6-v2", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
