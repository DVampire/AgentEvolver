"""MariTalk — from the Langflow `maritalk` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class MaritalkMaritalkPlugin(LLMPlugin):
    name: str = "maritalk.maritalk"
    display_name: str = 'MariTalk'
    description: str = 'Generates text using MariTalk LLMs.'
    kind: str = "model"
    bundle: str = "maritalk"
    bundle_label: str = 'MariTalk'
    source: str = "langflow/bundles/maritalk"
    status: str = "complete"
    default_base_url: str = ""
    key_env: str = "MARITALK_API_KEY"

    def _model(self, **cfg: Any) -> Any:
        from langchain_community.chat_models import ChatMaritalk
        key = self._secret(cfg.get("api_key"), self.key_env)
        if not key:
            raise ValueError("no API key (set api_key or MARITALK_API_KEY).")
        return ChatMaritalk(model=cfg.get("model_name"), api_key=key,
                            temperature=cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "sabia-3", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
