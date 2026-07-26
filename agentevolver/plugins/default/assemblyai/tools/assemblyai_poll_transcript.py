"""AssemblyAI Poll Transcript — from the Langflow `assemblyai` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.assemblyai._base import AssemblyAIPlugin


@PLUGIN.register_module(force=True)
class AssemblyaiAssemblyaiPollTranscriptPlugin(AssemblyAIPlugin):
    name: str = "assemblyai.assemblyai_poll_transcript"
    display_name: str = 'AssemblyAI Poll Transcript'
    description: str = 'Poll for the status of a transcription job using AssemblyAI'
    kind: str = "tool"
    bundle: str = "assemblyai"
    bundle_label: str = "AssemblyAI"
    source: str = "langflow/bundles/assemblyai"
    status: str = "complete"

    async def __call__(self, transcript_id: str = "", api_key: str = "", **kwargs) -> Response:
        try:
            aai = self._aai(api_key)
            if not transcript_id:
                return self._fail("assemblyai.poll_transcript: 'transcript_id' is required.")
            t = aai.Transcript.get_by_id(transcript_id)
            return self._ok(f"Transcript {transcript_id}: {t.status}.",
                            transcript_id=transcript_id, status=str(t.status), text=t.text)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"assemblyai.assemblyai_poll_transcript: {type(exc).__name__}: {exc}")
