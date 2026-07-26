"""EmpirioLabs AI — from the Langflow `empiriolabs` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class EmpiriolabsEmpiriolabsPlugin(BundlePlugin):
    name: str = "empiriolabs.empiriolabs"
    display_name: str = 'EmpirioLabs AI'
    description: str = 'Generates text using EmpirioLabs AI LLMs (OpenAI compatible).'
    kind: str = "tool"
    bundle: str = "empiriolabs"
    bundle_label: str = 'EmpirioLabs'
    category: str = "agent"
    source: str = "langflow/bundles/empiriolabs"
    status: str = "complete"

    async def __call__(self, prompt: str = "", model: str = "", api_key: str = "", **kwargs) -> Response:
        key = self._secret(api_key, "EMPIRIOLABS_API_KEY")
        if not prompt or not key:
            return self._fail("empiriolabs: 'prompt' and api_key are required.")
        try:
            from langchain_openai import ChatOpenAI
            model_ = ChatOpenAI(model=model or "gpt-4o-mini", api_key=key,
                               base_url="https://api.empiriolabs.ai/v1")
            out = model_.invoke(prompt)
            text = out.content if hasattr(out, "content") else str(out)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"empiriolabs: {type(exc).__name__}: {exc}")
        return self._ok(str(text), text=str(text))
