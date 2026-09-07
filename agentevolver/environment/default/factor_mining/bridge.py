"""File-only validation client; held-out data and grading stay in BenchmarkManager."""

import asyncio
import json
import uuid
from pathlib import Path

from agentevolver.utils.file_utils import atomic_write_text


async def request_validation(directory: str | Path, payload: dict, *, timeout=120) -> dict:
    root = Path(directory)
    if not (root / "requests").is_dir() or not (root / "responses").is_dir():
        raise RuntimeError("validation bridge is not prepared by the benchmark")
    identity = uuid.uuid4().hex
    text = json.dumps(payload, allow_nan=False)
    if len(text.encode()) > 64 * 1024:
        raise ValueError("validation request exceeds 64 KiB")
    atomic_write_text(root / "requests" / f"{identity}.json", text)
    response = root / "responses" / f"{identity}.json"
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if response.is_file() and not response.is_symlink():
            result = json.loads(response.read_text())
            if not result.get("success"):
                raise ValueError(result.get("message", "validation failed"))
            return result
        await asyncio.sleep(.05)
    raise TimeoutError("host validation did not respond; request is retained for audit")
