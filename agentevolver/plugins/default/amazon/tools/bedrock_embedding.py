"""Amazon Bedrock Embeddings — from the Langflow `amazon` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class AmazonAmazonBedrockEmbeddingPlugin(EmbeddingPlugin):
    name: str = "amazon.amazon_bedrock_embedding"
    display_name: str = 'Amazon Bedrock Embeddings'
    description: str = 'Generate embeddings using Amazon Bedrock models.'
    kind: str = "embedding"
    bundle: str = "amazon"
    bundle_label: str = 'Amazon Bedrock'
    source: str = "langflow/bundles/amazon"
    status: str = "complete"

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_aws import BedrockEmbeddings
        return BedrockEmbeddings(model_id=cfg.get("model_name"),
                                region_name=self._secret("", "AWS_REGION") or "us-east-1")

    async def __call__(self, text: str = "", model_name: str = "amazon.titan-embed-text-v1", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
