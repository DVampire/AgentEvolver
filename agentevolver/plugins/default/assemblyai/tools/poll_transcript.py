"""AssemblyAI Poll Transcript."""

from agentevolver.response.types import Response
from agentevolver.plugins.default.assemblyai._base import AssemblyaiToolBase


class AssemblyaiPollTranscriptTool(AssemblyaiToolBase):
    """AssemblyAI Poll Transcript."""

    name: str = 'assemblyai_poll_transcript'
    display_name: str = 'AssemblyAI Poll Transcript'
    description: str = 'Poll for the status of a transcription job using AssemblyAI'

    output = {'transcript_id': 'text', 'status': 'text', 'text': 'text'}


    def _render(self, data):
        return f"Transcript {data['transcript_id']}: {data['status']}."

    async def __call__(self, transcript_id: str = "", api_key: str = "", **kwargs) -> Response:
        try:
            aai = self._aai(api_key)
            if not transcript_id:
                return self._fail("assemblyai.poll_transcript: 'transcript_id' is required.")
            t = aai.Transcript.get_by_id(transcript_id)
            return self._ok(transcript_id=transcript_id, status=str(t.status), text=t.text)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"assemblyai.assemblyai_poll_transcript: {type(exc).__name__}: {exc}")
