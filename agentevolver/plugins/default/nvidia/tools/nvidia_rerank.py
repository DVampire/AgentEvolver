"""NVIDIA Rerank — from the Langflow `nvidia` rerank bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import RerankPlugin


@PLUGIN.register_module(force=True)
class NvidiaNvidiaRerankPlugin(RerankPlugin):
    name: str = "nvidia.nvidia_rerank"
    display_name: str = 'NVIDIA Rerank'
    description: str = 'Rerank documents using the NVIDIA API.'
    kind: str = "rerank"
    bundle: str = "nvidia"
    bundle_label: str = 'NVIDIA'
    source: str = "langflow/bundles/nvidia"
    status: str = "complete"
    key_env: str = "NVIDIA_API_KEY"

    def _reranker(self, **cfg: Any) -> Any:
        from langchain_nvidia_ai_endpoints import NVIDIARerank
        key = self._secret(cfg.get("api_key"), self.key_env)
        if not key:
            raise ValueError("no API key (set api_key or NVIDIA_API_KEY).")
        return NVIDIARerank(model=cfg.get("model_name"), api_key=key, top_n=cfg.get("top_n", 3))

    async def __call__(self, query: str = "", documents: Optional[List[str]] = None,
                       model_name: str = "nvidia/nv-rerankqa-mistral-4b-v3", api_key: str = "", top_n: int = 3, **kwargs) -> Response:
        return await self._rerank(query=query, documents=documents, top_n=int(top_n),
                                  model_name=model_name, api_key=api_key)
