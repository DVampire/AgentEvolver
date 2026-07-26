"""Cohere Language Models — from the Langflow `cohere` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class CohereCohereModelsPlugin(LLMPlugin):
    name: str = "cohere.cohere_models"
    display_name: str = 'Cohere Language Models'
    description: str = 'Generate text using Cohere LLMs.'
    kind: str = "model"
    bundle: str = "cohere"
    bundle_label: str = 'Cohere'
    source: str = "langflow/bundles/cohere"
    status: str = "complete"
    default_base_url: str = ""
    key_env: str = "COHERE_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        from langchain_cohere import ChatCohere
        key = self._secret(cfg.get("api_key"), self.key_env)
        if not key:
            raise ValueError("no API key (set api_key or COHERE_API_KEY).")
        return ChatCohere(model=cfg.get("model_name"), cohere_api_key=key,
                          temperature=cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "command-r-plus", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
