"""Small, reversible snapshots for manager-owned file write/edit operations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from agentevolver.paths import P, path_manager
from agentevolver.permission import Operation, PermissionRequest


MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


def _root() -> str:
    roots = path_manager.session_roots()
    if roots:
        return str(roots["log"])
    raise RuntimeError("a file checkpoint requires a bound session log root")


def capture_file_checkpoint(
    execution: Any, request: PermissionRequest,
) -> Dict[str, Any]:
    """Save the exact pre-write bytes; fail before mutation if that is impossible."""
    if request.op != Operation.WRITE:
        return {}
    target = Path(request.target).resolve()
    existed = target.exists()
    if existed and not target.is_file():
        raise RuntimeError(f"cannot checkpoint non-file target {target}")
    content = target.read_bytes() if existed else b""
    if len(content) > MAX_CHECKPOINT_BYTES:
        raise RuntimeError(
            f"refusing uncheckpointed write to {target}: existing file is "
            f"{len(content):,} bytes (limit {MAX_CHECKPOINT_BYTES:,})"
        )
    checkpoint_id = str(execution.token)
    payload = {
        "schema_version": 1,
        "checkpoint_id": checkpoint_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": execution.session_id,
        "tool_name": execution.tool_name,
        "call_id": execution.call_id,
        "target": str(target),
        "existed": existed,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    destination = path_manager.under(
        _root(), P.LOG_WORKSPACE_CHECKPOINT,
        filename=f"{checkpoint_id}.json", create=True,
    )
    temporary = path_manager.entry_under(
        destination.parent, f".{destination.name}.{os.getpid()}.tmp",
    )
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "id": checkpoint_id,
        "path": str(destination),
        "target": str(target),
        "existed": existed,
    }


def restore_file_checkpoint(checkpoint_path: str) -> str:
    """Restore one exact snapshot; intended for recovery commands and tests."""
    with open(checkpoint_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported workspace checkpoint schema")
    target = Path(str(payload["target"]))
    content = base64.b64decode(payload.get("content_base64") or "", validate=True)
    if hashlib.sha256(content).hexdigest() != payload.get("sha256"):
        raise ValueError("workspace checkpoint content hash mismatch")
    if payload.get("existed"):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = path_manager.entry_under(
            target.parent, f".{target.name}.restore-{os.getpid()}",
        )
        try:
            with open(temporary, "xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    elif target.exists():
        if not target.is_file():
            raise RuntimeError(f"refusing to remove non-file target {target}")
        target.unlink()
    return str(target)


__all__ = ["capture_file_checkpoint", "restore_file_checkpoint"]
