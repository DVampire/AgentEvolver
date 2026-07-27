"""Google Generative AI — from the Langflow `google` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleGenerativeAiPlugin(LLMPlugin):
    name: str = "google.google_generative_ai"
    display_name: str = 'Google Generative AI'
    description: str = 'Generate text using Google Generative AI.'
    kind: str = "model"
    bundle: str = "google"
    bundle_label: str = 'Google'
    source: str = "langflow/bundles/google"
    status: str = "complete"

    def _model(self, **cfg: Any) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI
        key = self._secret(cfg.get("api_key"), "GOOGLE_API_KEY", "GEMINI_API_KEY")
        if not key:
            raise ValueError("no API key (set api_key or GOOGLE_API_KEY).")
        return ChatGoogleGenerativeAI(model=cfg.get("model_name"), google_api_key=key,
                                     temperature=cfg.get("temperature", 0.1))

    async def __call__(self, prompt: str = "", model_name: str = "gemini-1.5-flash", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
