"""Pure record-transform processors.

Each accepts records either directly (``records=[...]``) or wrapped in an
upstream capability's ``data`` envelope (``data={"records": [...]}``) — so a
``datasource`` node can feed a ``process`` node with ``${source.data}`` without
the user hand-threading the ``.records`` sub-path.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.registry import PROCESS
from agentevolver.response.types import Response, ResponseType
from agentevolver.process.types import Processor


def _coerce_records(records: Any, data: Any) -> List[Dict[str, Any]]:
    """Pull a list of record dicts out of whatever the upstream node handed us.

    Connecting a capability's ``data`` output port yields the whole
    ``{records, count, …}`` envelope, so ``records`` may arrive as that dict —
    unwrap it. Also accepts a JSON-string list or a separate ``data`` envelope.
    """
    records = _as_list(records)
    if isinstance(records, dict) and isinstance(records.get("records"), list):
        return records["records"]
    if isinstance(records, list):
        return records
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    if isinstance(data, list):
        return data
    return []


def _as_list(value: Any) -> Any:
    """Coerce a canvas-compiled arg to a list.

    List/object ports compile to JSON-string args in the workflow language, so a
    ``["a","b"]`` literal arrives as the string ``'["a","b"]'``. Parse it back;
    leave genuine lists and non-JSON strings untouched.
    """
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return value
    return value


@PROCESS.register_module(force=True)
class SelectFieldsProcessor(Processor):
    """Keep only the named fields on each record (drop the rest)."""

    name: str = "select_fields"
    description: str = "Project each record down to a chosen list of fields."
    instruction: str = (
        "## Function\nKeep only ``fields`` on each record.\n\n"
        "## Parameters\n- records (list) OR data ({records}): input rows.\n"
        "- fields (list[str]): field names to keep (required)."
    )
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"canvas_category": "process"})

    async def __call__(self, records: Any = None, data: Any = None,
                       fields: Optional[List[str]] = None, **kwargs) -> Response:
        rows = _coerce_records(records, data)
        keep = _as_list(fields) or []
        if not isinstance(keep, list) or not keep:
            return Response(type=ResponseType.TOOL, success=False,
                            message="select_fields: 'fields' is required.")
        out = [{k: row.get(k) for k in keep} for row in rows if isinstance(row, dict)]
        return Response(type=ResponseType.TOOL, success=True,
                        message=f"Selected {len(keep)} field(s) across {len(out)} record(s).",
                        data={"records": out, "count": len(out)})


@PROCESS.register_module(force=True)
class HeadProcessor(Processor):
    """Take the first ``n`` records."""

    name: str = "head"
    description: str = "Keep only the first n records."
    instruction: str = (
        "## Function\nReturn the first ``n`` records.\n\n"
        "## Parameters\n- records (list) OR data ({records}): input rows.\n"
        "- n (int): how many to keep (default 10)."
    )
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"canvas_category": "process"})

    async def __call__(self, records: Any = None, data: Any = None, n: int = 10, **kwargs) -> Response:
        rows = _coerce_records(records, data)
        try:
            n = max(0, int(n))
        except (TypeError, ValueError):
            n = 10
        out = rows[:n]
        return Response(type=ResponseType.TOOL, success=True,
                        message=f"Kept first {len(out)} of {len(rows)} record(s).",
                        data={"records": out, "count": len(out)})


@PROCESS.register_module(force=True)
class SortRecordsProcessor(Processor):
    """Sort records by a field."""

    name: str = "sort_records"
    description: str = "Sort records ascending/descending by a field."
    instruction: str = (
        "## Function\nSort records by ``key``.\n\n"
        "## Parameters\n- records (list) OR data ({records}): input rows.\n"
        "- key (str): field to sort by (required).\n- descending (bool): default false."
    )
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"canvas_category": "process"})

    async def __call__(self, records: Any = None, data: Any = None,
                       key: str = "", descending: bool = False, **kwargs) -> Response:
        rows = _coerce_records(records, data)
        if not key:
            return Response(type=ResponseType.TOOL, success=False,
                            message="sort_records: 'key' is required.")
        out = sorted(
            [row for row in rows if isinstance(row, dict)],
            key=lambda row: (row.get(key) is None, row.get(key)),
            reverse=bool(descending),
        )
        return Response(type=ResponseType.TOOL, success=True,
                        message=f"Sorted {len(out)} record(s) by '{key}'"
                                f"{' desc' if descending else ' asc'}.",
                        data={"records": out, "count": len(out)})
