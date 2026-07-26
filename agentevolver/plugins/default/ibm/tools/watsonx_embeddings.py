"""IBM watsonx.ai Embeddings — from the Langflow `ibm` embeddings bundle (ported)."""

from typing import Any

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import EmbeddingPlugin


@PLUGIN.register_module(force=True)
class IbmWatsonxEmbeddingsPlugin(EmbeddingPlugin):
    name: str = "ibm.watsonx_embeddings"
    display_name: str = 'IBM watsonx.ai Embeddings'
    description: str = 'Generate embeddings using IBM watsonx.ai models.'
    kind: str = "embedding"
    bundle: str = "ibm"
    bundle_label: str = 'IBM watsonx'
    source: str = "langflow/bundles/ibm"
    status: str = "complete"

    def _embeddings(self, **cfg: Any) -> Any:
        from langchain_ibm import WatsonxEmbeddings
        key = self._secret(cfg.get("api_key"), "WATSONX_APIKEY")
        url = cfg.get("base_url") or self._secret("", "WATSONX_URL")
        project = self._secret("", "WATSONX_PROJECT_ID")
        if not key or not url or not project:
            raise ValueError("watsonx needs WATSONX_APIKEY, WATSONX_URL, WATSONX_PROJECT_ID.")
        return WatsonxEmbeddings(model_id=cfg.get("model_name"), url=url, apikey=key, project_id=project)

    async def __call__(self, text: str = "", model_name: str = "ibm/slate-125m-english-rtrvr", api_key: str = "",
                       base_url: str = "", **kwargs) -> Response:
        return await self._embed(text=text, model_name=model_name, api_key=api_key, base_url=base_url)
