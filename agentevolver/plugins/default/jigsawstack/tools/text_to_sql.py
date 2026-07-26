"""Text to SQL — from the Langflow `jigsawstack` bundle (ported)."""

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.default.jigsawstack._base import JigsawStackPlugin


@PLUGIN.register_module(force=True)
class JigsawstackTextToSqlPlugin(JigsawStackPlugin):
    name: str = "jigsawstack.text_to_sql"
    display_name: str = 'Text to SQL'
    description: str = 'Convert natural language to SQL queries using JigsawStack AI'
    kind: str = "tool"
    bundle: str = "jigsawstack"
    bundle_label: str = "JigsawStack"
    source: str = "langflow/bundles/jigsawstack"
    status: str = "complete"

    async def __call__(self, prompt: str = "", sql_schema: str = "", api_key: str = "", **kwargs) -> Response:
        params = {"prompt": prompt, "sql_schema": sql_schema}
        return await self._run("text_to_sql", {k: v for k, v in params.items() if v not in (None, "", [])}, api_key)
