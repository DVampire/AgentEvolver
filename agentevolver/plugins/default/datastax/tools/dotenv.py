"""Dotenv — from the Langflow `datastax` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DatastaxDotenvPlugin(BundlePlugin):
    name: str = "datastax.dotenv"
    display_name: str = 'Dotenv'
    description: str = 'Load .env file into env vars'
    kind: str = "tool"
    bundle: str = "datastax"
    bundle_label: str = 'DataStax'
    category: str = "data"
    source: str = "langflow/bundles/datastax"
    status: str = "complete"

    async def __call__(self, dotenv_content: str = "", **kwargs) -> Response:
        import io
        from dotenv import dotenv_values
        if not dotenv_content.strip():
            return self._fail("dotenv: 'dotenv_content' is required.")
        values = dict(dotenv_values(stream=io.StringIO(dotenv_content)))
        return self._ok(f"Parsed {len(values)} environment variables.", variables=values, count=len(values))
