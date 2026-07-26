"""Convert Astra DB to Pegasus Input — from the Langflow `twelvelabs` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class TwelvelabsConvertAstraResultsPlugin(BundlePlugin):
    name: str = "twelvelabs.convert_astra_results"
    display_name: str = 'Convert Astra DB to Pegasus Input'
    description: str = 'Converts Astra DB search results to inputs compatible with TwelveLabs Pegasus.'
    kind: str = "tool"
    bundle: str = "twelvelabs"
    bundle_label: str = 'TwelveLabs'
    category: str = "data"
    source: str = "langflow/bundles/twelvelabs"
    status: str = "complete"

    async def __call__(self, results: Optional[list] = None, **kwargs) -> Response:
        rows = results or []
        records = [{"content": str(r)} for r in rows]
        return self._ok(f"Converted {len(records)} Astra results.", records=records, count=len(records))
