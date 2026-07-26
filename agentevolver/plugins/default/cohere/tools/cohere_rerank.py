"""Cohere Rerank — from the Langflow `cohere` rerank bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import RerankPlugin


@PLUGIN.register_module(force=True)
class CohereCohereRerankPlugin(RerankPlugin):
    name: str = "cohere.cohere_rerank"
    display_name: str = 'Cohere Rerank'
    description: str = 'Rerank documents using the Cohere API.'
    kind: str = "rerank"
    bundle: str = "cohere"
    bundle_label: str = 'Cohere'
    source: str = "langflow/bundles/cohere"
    status: str = "complete"
    key_env: str = "COHERE_API_KEY"

    def _reranker(self, **cfg: Any) -> Any:
        from langchain_cohere import CohereRerank
        key = self._secret(cfg.get("api_key"), self.key_env)
        if not key:
            raise ValueError("no API key (set api_key or COHERE_API_KEY).")
        return CohereRerank(model=cfg.get("model_name"), cohere_api_key=key, top_n=cfg.get("top_n", 3))

    async def __call__(self, query: str = "", documents: Optional[List[str]] = None,
                       model_name: str = "rerank-english-v3.0", api_key: str = "", top_n: int = 3, **kwargs) -> Response:
        return await self._rerank(query=query, documents=documents, top_n=int(top_n),
                                  model_name=model_name, api_key=api_key)
