"""Novita AI — from the Langflow `novita` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class NovitaNovitaPlugin(LLMPlugin):
    name: str = "novita.novita"
    display_name: str = 'Novita AI'
    description: str = 'Generates text using Novita AI LLMs (OpenAI compatible).'
    kind: str = "model"
    bundle: str = "novita"
    bundle_label: str = 'Novita AI'
    source: str = "langflow/bundles/novita"
    status: str = "complete"
    default_base_url: str = "https://api.novita.ai/v3/openai"
    key_env: str = "NOVITA_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        return self._openai_compatible(cfg.get("model_name"), cfg.get("api_key", ""),
                                       cfg.get("base_url", ""), cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "meta-llama/llama-3.1-8b-instruct", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
