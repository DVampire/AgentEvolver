"""Azure OpenAI — from the Langflow `azure` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class AzureAzureOpenaiPlugin(LLMPlugin):
    name: str = "azure.azure_openai"
    display_name: str = 'Azure OpenAI'
    description: str = 'Generate text using Azure OpenAI LLMs.'
    kind: str = "model"
    bundle: str = "azure"
    bundle_label: str = 'Azure OpenAI'
    source: str = "langflow/bundles/azure"
    status: str = "complete"
    default_base_url: str = ""
    key_env: str = "AZURE_OPENAI_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        from langchain_openai import AzureChatOpenAI
        key = self._secret(cfg.get("api_key"), self.key_env)
        endpoint = cfg.get("base_url") or self._secret("", "AZURE_OPENAI_ENDPOINT")
        if not key or not endpoint:
            raise ValueError("Azure needs api_key (AZURE_OPENAI_API_KEY) and base_url (AZURE_OPENAI_ENDPOINT).")
        return AzureChatOpenAI(azure_deployment=cfg.get("model_name"), api_key=key,
                               azure_endpoint=endpoint, api_version="2024-08-01-preview",
                               temperature=cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "gpt-4o-mini", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
