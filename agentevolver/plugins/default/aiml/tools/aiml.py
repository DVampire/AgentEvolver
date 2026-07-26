"""AI/ML API — from the Langflow `aiml` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class AimlAimlPlugin(LLMPlugin):
    name: str = "aiml.aiml"
    display_name: str = 'AI/ML API'
    description: str = 'Generates text using AI/ML API LLMs.'
    kind: str = "model"
    bundle: str = "aiml"
    bundle_label: str = 'AI/ML API'
    source: str = "langflow/bundles/aiml"
    status: str = "complete"
    default_base_url: str = "https://api.aimlapi.com/v1"
    key_env: str = "AIML_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        return self._openai_compatible(cfg.get("model_name"), cfg.get("api_key", ""),
                                       cfg.get("base_url", ""), cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "gpt-4o-mini", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
