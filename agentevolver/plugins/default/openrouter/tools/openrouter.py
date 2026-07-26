"""OpenRouter — from the Langflow `openrouter` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class OpenrouterOpenrouterPlugin(LLMPlugin):
    name: str = "openrouter.openrouter"
    display_name: str = 'OpenRouter'
    description: str = 'OpenRouter'
    kind: str = "model"
    bundle: str = "openrouter"
    bundle_label: str = 'OpenRouter'
    source: str = "langflow/bundles/openrouter"
    status: str = "complete"
    default_base_url: str = "https://openrouter.ai/api/v1"
    key_env: str = "OPENROUTER_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        return self._openai_compatible(cfg.get("model_name"), cfg.get("api_key", ""),
                                       cfg.get("base_url", ""), cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "openai/gpt-4o-mini", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
