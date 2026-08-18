"""AssemblyAI LeMUR."""

from agentevolver.response.types import Response
from agentevolver.plugins.default.assemblyai._base import AssemblyaiToolBase


class AssemblyaiLemurTool(AssemblyaiToolBase):
    """AssemblyAI LeMUR."""

    name: str = 'assemblyai_lemur'
    display_name: str = 'AssemblyAI LeMUR'
    description: str = 'Apply Large Language Models to spoken data using the AssemblyAI LeMUR framework'

    output = {'response': 'object'}

    def _render(self, data):
        return 'LeMUR task completed.'

    async def __call__(self, transcript_ids: list = None, prompt: str = "", final_model: str = "default", api_key: str = "", **kwargs) -> Response:
        try:
            aai = self._aai(api_key)
            ids = [i for i in (transcript_ids or []) if i]
            if not ids or not prompt:
                return self._fail("assemblyai.lemur: 'transcript_ids' and 'prompt' are required.")
            lemur = aai.Lemur()
            result = lemur.task(prompt=prompt, final_model=final_model, transcript_ids=ids)
            return self._ok(response=getattr(result, 'response', str(result)))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"assemblyai.assemblyai_lemur: {type(exc).__name__}: {exc}")
