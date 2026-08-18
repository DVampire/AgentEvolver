"""Dotenv."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class DatastaxDotenvTool(PluginTool):
    """Dotenv."""

    name: str = 'dotenv'
    display_name: str = 'Dotenv'
    description: str = 'Load .env file into env vars'

    output = {'variables': 'any', 'count': 'any'}

    def _render(self, data):
        return f"Parsed {data['count']} environment variables."

    async def __call__(self, dotenv_content: str = "", **kwargs) -> Response:
        import io
        from dotenv import dotenv_values
        if not dotenv_content.strip():
            return self._fail("dotenv: 'dotenv_content' is required.")
        values = dict(dotenv_values(stream=io.StringIO(dotenv_content)))
        return self._ok(variables=values, count=len(values))
