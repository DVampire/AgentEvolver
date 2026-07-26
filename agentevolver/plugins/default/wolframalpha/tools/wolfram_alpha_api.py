"""WolframAlpha API — from the Langflow `wolframalpha` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class WolframalphaWolframAlphaApiPlugin(BundlePlugin):
    name: str = "wolframalpha.wolfram_alpha_api"
    display_name: str = 'WolframAlpha API'
    description: str = 'WolframAlpha API'
    kind: str = "tool"
    bundle: str = "wolframalpha"
    bundle_label: str = 'WolframAlpha'
    category: str = "data"
    source: str = "langflow/bundles/wolframalpha"
    status: str = "complete"

    async def __call__(self, input_value: str = "", app_id: str = "", **kwargs) -> Response:
        q = str(input_value or "").strip()
        key = self._secret(app_id, "WOLFRAM_ALPHA_APPID")
        if not q or not key:
            return self._fail("wolframalpha: 'input_value' and app_id (WOLFRAM_ALPHA_APPID) are required.")
        try:
            from langchain_community.utilities.wolfram_alpha import WolframAlphaAPIWrapper
            out = WolframAlphaAPIWrapper(wolfram_alpha_appid=key).run(q)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"wolframalpha: {type(exc).__name__}: {exc}")
        return self._ok(str(out), query=q, result=str(out))
