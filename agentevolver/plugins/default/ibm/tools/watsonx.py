"""IBM watsonx.ai — from the Langflow `ibm` LLM bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import LLMPlugin


@PLUGIN.register_module(force=True)
class IbmWatsonxPlugin(LLMPlugin):
    name: str = "ibm.watsonx"
    display_name: str = 'IBM watsonx.ai'
    description: str = 'Generate text using IBM watsonx.ai foundation models.'
    kind: str = "model"
    bundle: str = "ibm"
    bundle_label: str = 'IBM watsonx'
    source: str = "langflow/bundles/ibm"
    status: str = "complete"

    def _model(self, **cfg: Any) -> Any:
        from langchain_ibm import ChatWatsonx
        key = self._secret(cfg.get("api_key"), "WATSONX_APIKEY")
        url = cfg.get("base_url") or self._secret("", "WATSONX_URL")
        project = self._secret("", "WATSONX_PROJECT_ID")
        if not key or not url or not project:
            raise ValueError("watsonx needs WATSONX_APIKEY, WATSONX_URL, WATSONX_PROJECT_ID.")
        return ChatWatsonx(model_id=cfg.get("model_name"), url=url, apikey=key, project_id=project)

    async def __call__(self, prompt: str = "", model_name: str = "ibm/granite-13b-instruct-v2", api_key: str = "",
                       base_url: str = "", temperature: float = 0.1, **kwargs) -> Response:
        return await self._generate(prompt=prompt, model_name=model_name, api_key=api_key,
                                    base_url=base_url, temperature=temperature)
