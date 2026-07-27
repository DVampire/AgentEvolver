"""Video File."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class TwelvelabsVideoFileTool(PluginTool):
    """Video File."""

    name: str = 'video_file'
    display_name: str = 'Video File'
    description: str = 'Load a video file in common video formats.'
    category: str = 'files'

    async def __call__(self, file_path: str = "", **kwargs) -> Response:
        import os as _os
        if not file_path or not _os.path.exists(file_path):
            return self._fail("twelvelabs.video_file: a valid 'file_path' is required.")
        return self._ok(f"Video file ready: {file_path}.", path=file_path,
                        size=_os.path.getsize(file_path))
