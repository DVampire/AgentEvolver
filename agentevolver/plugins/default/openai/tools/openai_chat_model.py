"""OpenAI — from the Langflow `openai` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class OpenaiOpenaiChatModelPlugin(LLMPlugin):
    name: str = "openai.openai_chat_model"
    display_name: str = 'OpenAI'
    description: str = 'Generates text using OpenAI LLMs.'
    kind: str = "model"
    bundle: str = "openai"
    bundle_label: str = 'OpenAI'
    source: str = "langflow/bundles/openai"
    status: str = "complete"
    default_base_url: str = ""
    key_env: str = "OPENAI_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        return self._openai_compatible(cfg.get("model_name"), cfg.get("api_key", ""),
                                       cfg.get("base_url", ""), cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "gpt-4o-mini", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
