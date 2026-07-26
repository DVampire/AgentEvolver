"""AssemblyAI LeMUR — from the Langflow `assemblyai` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.assemblyai._base import AssemblyAIPlugin


@PLUGIN.register_module(force=True)
class AssemblyaiAssemblyaiLemurPlugin(AssemblyAIPlugin):
    name: str = "assemblyai.assemblyai_lemur"
    display_name: str = 'AssemblyAI LeMUR'
    description: str = 'Apply Large Language Models to spoken data using the AssemblyAI LeMUR framework'
    kind: str = "tool"
    bundle: str = "assemblyai"
    bundle_label: str = "AssemblyAI"
    source: str = "langflow/bundles/assemblyai"
    status: str = "complete"

    async def __call__(self, transcript_ids: list = None, prompt: str = "", final_model: str = "default", api_key: str = "", **kwargs) -> Response:
        try:
            aai = self._aai(api_key)
            ids = [i for i in (transcript_ids or []) if i]
            if not ids or not prompt:
                return self._fail("assemblyai.lemur: 'transcript_ids' and 'prompt' are required.")
            lemur = aai.Lemur()
            result = lemur.task(prompt=prompt, final_model=final_model, transcript_ids=ids)
            return self._ok("LeMUR task completed.", response=getattr(result, "response", str(result)))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"assemblyai.assemblyai_lemur: {type(exc).__name__}: {exc}")
