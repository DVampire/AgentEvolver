"""Data Manager — persist/load pipeline datasets.

The ``data`` module owns AgentEvolver's dataset *assets*. Its registry (DATASET)
holds static benchmark loaders (gsm8k, aime, …); this manager adds the two
data-pipeline *operations* the canvas needs so a flow can close the loop:

    [datasource] → [process] → [data · save_dataset] → (reusable dataset)

``save_dataset`` writes the upstream records as a named JSONL dataset and
returns its path; ``load_dataset`` reads one back. They are exposed as two named
operations (the dataset name is an argument), which is what lets the canvas bind
them like any other callable capability. Both return the canonical
``{message, data, files}`` envelope.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.response.types import Response, ResponseType
from agentevolver.utils import assemble_workspace_path

# The named operations this manager exposes as canvas ``data`` nodes.
DATA_OPERATIONS: Dict[str, str] = {
    "save_dataset": "Persist upstream records as a named JSONL dataset.",
    "load_dataset": "Load a previously saved dataset back into the flow.",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _coerce_records(records: Any, data: Any) -> List[Dict[str, Any]]:
    """Pull a list of records out of an upstream arg (list, JSON string, or
    a ``{records: [...]}`` data envelope)."""
    if isinstance(records, str):
        text = records.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                records = json.loads(text)
            except (ValueError, TypeError):
                records = None
    # A connected ``data`` output port hands over the whole {records, …} envelope.
    if isinstance(records, dict) and isinstance(records.get("records"), list):
        return records["records"]
    if isinstance(records, list):
        return records
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    if isinstance(data, list):
        return data
    return []


class DataManager(BaseModel):
    """Save/load named datasets under a datasets directory."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: Optional[str] = Field(default=None, description="Directory holding saved datasets.")

    async def initialize(self, data_names: Optional[List[str]] = None) -> None:
        """Resolve the datasets directory (created lazily on first save)."""
        self.base_dir = assemble_workspace_path(os.path.join(config.log_root, "datasets"))
        logger.info(f"| 🗂️ Data manager datasets directory: {self.base_dir}")
        logger.info("| ✅ Data manager initialization completed")

    async def list(self) -> List[str]:
        """The callable data operations (not the datasets themselves)."""
        return list(DATA_OPERATIONS.keys())

    async def get_info(self, name: str) -> Optional["_OperationInfo"]:
        """Descriptor for a data operation, or None if unknown."""
        description = DATA_OPERATIONS.get(name)
        return _OperationInfo(name=name, description=description) if description else None

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """Data operations accept free-form args; no strict call schema."""
        return None

    def _path(self, dataset_name: str) -> str:
        safe = _SAFE_NAME.sub("_", str(dataset_name or "").strip()).strip("_") or "dataset"
        return os.path.join(self.base_dir or ".", f"{safe}.jsonl")

    async def __call__(self, name: str, input: Dict[str, Any], ctx: Any = None, **kwargs) -> Response:
        """Dispatch a data operation (save_dataset / load_dataset)."""
        payload = input or {}
        if name == "save_dataset":
            return self._save(payload)
        if name == "load_dataset":
            return self._load(payload)
        return Response(type=ResponseType.TOOL, success=False, message=f"Unknown data operation: {name}")

    def _save(self, payload: Dict[str, Any]) -> Response:
        dataset_name = payload.get("name") or payload.get("dataset")
        if not dataset_name:
            return Response(type=ResponseType.TOOL, success=False, message="save_dataset: 'name' is required.")
        records = _coerce_records(payload.get("records"), payload.get("data"))
        path = self._path(dataset_name)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            return Response(type=ResponseType.TOOL, success=False, message=f"save_dataset: write failed: {exc}")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"Saved {len(records)} record(s) to dataset '{dataset_name}'.",
            data={"dataset": str(dataset_name), "path": path, "count": len(records)},
            files=[path],
        )

    def _load(self, payload: Dict[str, Any]) -> Response:
        dataset_name = payload.get("name") or payload.get("dataset")
        if not dataset_name:
            return Response(type=ResponseType.TOOL, success=False, message="load_dataset: 'name' is required.")
        path = self._path(dataset_name)
        if not os.path.exists(path):
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"load_dataset: dataset '{dataset_name}' not found at {path}.")
        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except (OSError, ValueError) as exc:
            return Response(type=ResponseType.TOOL, success=False, message=f"load_dataset: read failed: {exc}")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"Loaded {len(records)} record(s) from dataset '{dataset_name}'.",
            data={"dataset": str(dataset_name), "path": path, "records": records, "count": len(records)},
            files=[path],
        )

    async def cleanup(self) -> None:
        """No resources to release (datasets persist on disk)."""


class _OperationInfo(BaseModel):
    """Minimal descriptor so validation/catalog can read a name + description."""

    name: str
    description: str = ""


data_manager = DataManager()
