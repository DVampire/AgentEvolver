"""Azure OpenAI Embeddings — from the Langflow `azure` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class AzureAzureOpenaiEmbeddingsPlugin(EmbeddingPlugin):
    name: str = "azure.azure_openai_embeddings"
    display_name: str = 'Azure OpenAI Embeddings'
    description: str = 'Generate embeddings using Azure OpenAI models.'
    kind: str = "embedding"
    bundle: str = "azure"
    bundle_label: str = 'Azure OpenAI'
    source: str = "langflow/bundles/azure"
    status: str = "complete"
    key_env: str = "AZURE_OPENAI_API_KEY"
    default_base_url: str = ""

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_openai import AzureOpenAIEmbeddings
        key = self._secret(cfg.get("api_key"), self.key_env)
        endpoint = cfg.get("base_url") or self._secret("", "AZURE_OPENAI_ENDPOINT")
        if not key or not endpoint:
            raise ValueError("Azure needs api_key (AZURE_OPENAI_API_KEY) and base_url (AZURE_OPENAI_ENDPOINT).")
        return AzureOpenAIEmbeddings(azure_deployment=cfg.get("model_name"), api_key=key,
                                    azure_endpoint=endpoint, api_version="2024-08-01-preview")

    async def __call__(self, text: str = "", model_name: str = "text-embedding-3-small", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
