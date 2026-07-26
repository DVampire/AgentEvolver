"""Vertex AI — from the Langflow `vertexai` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class VertexaiVertexaiPlugin(LLMPlugin):
    name: str = "vertexai.vertexai"
    display_name: str = 'Vertex AI'
    description: str = 'Generate text using Vertex AI LLMs.'
    kind: str = "model"
    bundle: str = "vertexai"
    bundle_label: str = 'Vertex AI'
    source: str = "langflow/bundles/vertexai"
    status: str = "complete"

    def _model(self, **cfg: Any) -> Any:
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(model_name=cfg.get("model_name"), temperature=cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "gemini-1.5-flash", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
