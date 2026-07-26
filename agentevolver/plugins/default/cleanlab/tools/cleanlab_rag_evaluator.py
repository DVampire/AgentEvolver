"""Cleanlab RAG Evaluator — from the Langflow `cleanlab` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class CleanlabCleanlabRagEvaluatorPlugin(BundlePlugin):
    name: str = "cleanlab.cleanlab_rag_evaluator"
    display_name: str = 'Cleanlab RAG Evaluator'
    description: str = 'Evaluates context, query, and response from a RAG pipeline using Cleanlab and outputs trust metrics.'
    kind: str = "tool"
    bundle: str = "cleanlab"
    bundle_label: str = 'Cleanlab'
    category: str = "evaluation"
    source: str = "langflow/bundles/cleanlab"
    status: str = "complete"

    async def __call__(self, query: str = "", context: str = "", response: str = "", api_key: str = "", model: str = "gpt-4o-mini", **kwargs) -> Response:
        key = self._secret(api_key, "CLEANLAB_TLM_API_KEY", "CLEANLAB_API_KEY")
        if not query or not response or not key:
            return self._fail("cleanlab.rag_evaluator: 'query', 'response' and api_key are required.")
        try:
            from cleanlab_tlm import TLM
            tlm = TLM(api_key=key, options={"model": model})
            full = f"Context: {context}\n\nQuery: {query}"
            result = tlm.get_trustworthiness_score(full, response)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"cleanlab.rag_evaluator: {type(exc).__name__}: {exc}")
        score = result.get("trustworthiness_score", 0.0) if isinstance(result, dict) else result
        return self._ok(f"RAG trust score: {score}.", score=score)
