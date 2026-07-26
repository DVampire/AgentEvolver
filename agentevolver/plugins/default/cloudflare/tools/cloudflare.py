"""Cloudflare Workers AI Embeddings — from the Langflow `cloudflare` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class CloudflareCloudflarePlugin(EmbeddingPlugin):
    name: str = "cloudflare.cloudflare"
    display_name: str = 'Cloudflare Workers AI Embeddings'
    description: str = 'Generate embeddings using Cloudflare Workers AI models.'
    kind: str = "embedding"
    bundle: str = "cloudflare"
    bundle_label: str = 'Cloudflare'
    category: str = "knowledge"
    source: str = "langflow/bundles/cloudflare"
    status: str = "complete"
    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_community.embeddings.cloudflare_workersai import CloudflareWorkersAIEmbeddings
        key = self._secret(cfg.get("api_key"), "CLOUDFLARE_API_TOKEN")
        account = cfg.get("account_id") or self._secret("", "CLOUDFLARE_ACCOUNT_ID")
        if not key or not account:
            raise ValueError("Cloudflare needs api_key (CLOUDFLARE_API_TOKEN) and account_id.")
        return CloudflareWorkersAIEmbeddings(account_id=account, api_token=key, model_name=cfg.get("model_name"))

    async def __call__(self, text: str = "", model_name: str = "@cf/baai/bge-base-en-v1.5", account_id: str = "", api_key: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, account_id=account_id, api_key=api_key)
