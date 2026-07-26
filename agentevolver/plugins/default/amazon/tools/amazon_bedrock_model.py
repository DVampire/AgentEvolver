"""Amazon Bedrock — from the Langflow `amazon` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class AmazonAmazonBedrockModelPlugin(LLMPlugin):
    name: str = "amazon.amazon_bedrock_model"
    display_name: str = 'Amazon Bedrock'
    description: str = 'Amazon Bedrock'
    kind: str = "model"
    bundle: str = "amazon"
    bundle_label: str = 'Amazon Bedrock'
    source: str = "langflow/bundles/amazon"
    status: str = "complete"

    def _model(self, **cfg: Any) -> Any:
        from langchain_aws import ChatBedrock
        return ChatBedrock(model_id=cfg.get("model_name"),
                          region_name=self._secret("", "AWS_REGION") or "us-east-1")

    async def __call__(self, prompt: str = "", model_name: str = "anthropic.claude-3-5-sonnet-20240620-v1:0", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
