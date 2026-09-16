"""Convert Astra DB to Pegasus Input."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class TwelvelabsConvertAstraResultsTool(PluginTool):
    """Convert Astra DB to Pegasus Input."""

    name: str = 'convert_astra_results'
    display_name: str = 'Convert Astra DB to Pegasus Input'
    description: str = 'Converts Astra DB search results to inputs compatible with TwelveLabs Pegasus.'

    output = {'records': 'list', 'count': 'any'}

    def _render(self, data):
        return f"Converted {data['count']} Astra results."

    async def __call__(self, results: Optional[list] = None, **kwargs) -> Response:
        rows = results or []
        records = [{"content": str(r)} for r in rows]
        return self._ok(records=records, count=len(records))
