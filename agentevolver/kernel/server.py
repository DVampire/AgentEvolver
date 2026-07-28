"""Kernel Manager Server

Runs one Jupyter kernel per project and executes code in it.

The kernel is a subprocess of the framework's own container, not a peer
container: the agent already runs arbitrary shell here (``bash_tool``), so a
separate container bought no isolation — and the one it used had no mounts, so
code could not read the files the agent had just written.
"""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.config import config
from agentevolver.kernel.types import KernelOutput, KernelResult
from agentevolver.logger import logger


class KernelManagerServer(BaseModel):
    """Start, reuse, and execute against one kernel per project."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    #: How long a single execution may run before it is interrupted.
    timeout_seconds: float = Field(default=300.0)
    #: Kernel to start. ``python3`` is the one every install has; other
    #: languages register their own kernelspec name.
    default_kernel: str = Field(default="python3")

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._kernels: Dict[str, object] = {}
        self._clients: Dict[str, object] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    # ---------------------------------------------------------------- lifecycle
    async def _ensure(self, key: str, kernel_name: str):
        """The project's live kernel, starting one if needed."""
        client = self._clients.get(key)
        if client is not None:
            return client

        from jupyter_client.manager import AsyncKernelManager  # noqa: PLC0415 — optional at import time

        # Start in the workspace so relative paths mean the same thing they do
        # to bash_tool and to the files pane.
        cwd = str(config.workspace_root or os.getcwd())
        os.makedirs(cwd, exist_ok=True)
        manager = AsyncKernelManager(kernel_name=kernel_name)
        await manager.start_kernel(cwd=cwd)
        client = manager.client()
        client.start_channels()
        await client.wait_for_ready(timeout=60)

        self._kernels[key] = manager
        self._clients[key] = client
        logger.info(f"| 🐍 Kernel started for {key} ({kernel_name}) in {cwd}")
        return client

    async def shutdown(self, key: str) -> bool:
        """Stop one project's kernel. True if one was running."""
        client = self._clients.pop(key, None)
        manager = self._kernels.pop(key, None)
        if client is not None:
            client.stop_channels()
        if manager is not None:
            try:
                await manager.shutdown_kernel(now=True)
            except Exception as exc:  # noqa: BLE001 — teardown must not raise
                logger.warning(f"| ⚠️ Kernel {key} did not shut down cleanly: {exc}")
        return manager is not None

    async def restart(self, key: str, kernel_name: Optional[str] = None) -> None:
        """Throw the interpreter state away and start fresh."""
        await self.shutdown(key)
        await self._ensure(key, kernel_name or self.default_kernel)

    async def cleanup(self) -> None:
        for key in list(self._clients):
            await self.shutdown(key)

    def running(self) -> List[str]:
        return list(self._clients)

    # ---------------------------------------------------------------- execute
    async def execute(self, code: str, *, key: str = "default",
                      kernel_name: Optional[str] = None) -> KernelResult:
        """Run ``code`` in the project's kernel and collect everything it produced.

        Serialized per project: a kernel executes one cell at a time, and two
        concurrent callers would otherwise interleave their iopub messages.
        """
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                client = await self._ensure(key, kernel_name or self.default_kernel)
            except Exception as exc:  # noqa: BLE001 — a missing kernel is a failed result
                return KernelResult(success=False, error=f"Could not start a kernel: {exc}")
            return await self._run(client, code)

    async def _run(self, client, code: str) -> KernelResult:
        msg_id = client.execute(code)
        outputs: List[KernelOutput] = []
        result = KernelResult()
        deadline = asyncio.get_event_loop().time() + self.timeout_seconds

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                await self._interrupt(client)
                result.success = False
                result.error = f"Execution exceeded {self.timeout_seconds:.0f}s and was interrupted."
                break
            try:
                message = await client.get_iopub_msg(timeout=remaining)
            except Exception:  # noqa: BLE001 — queue empty at the deadline
                continue
            # Messages from an earlier cell can still be draining; only this
            # execution's replies describe this execution.
            if (message.get("parent_header") or {}).get("msg_id") != msg_id:
                continue

            kind, content = message["msg_type"], message["content"]
            if kind == "stream":
                outputs.append(KernelOutput(type="stream", name=content.get("name"),
                                            data={"text/plain": content.get("text", "")}))
            elif kind in ("execute_result", "display_data", "update_display_data"):
                outputs.append(KernelOutput(
                    type="result" if kind == "execute_result" else "display",
                    data=dict(content.get("data") or {})))
                if kind == "execute_result":
                    result.execution_count = content.get("execution_count")
            elif kind == "error":
                traceback = "\n".join(content.get("traceback") or [])
                result.success = False
                result.error = _strip_ansi(traceback) or f"{content.get('ename')}: {content.get('evalue')}"
                outputs.append(KernelOutput(type="error", data={"text/plain": result.error}))
            elif kind == "status" and content.get("execution_state") == "idle":
                break

        result.outputs = outputs
        return result

    @staticmethod
    async def _interrupt(client) -> None:
        try:
            parent = getattr(client, "parent", None)
            if parent is not None and hasattr(parent, "interrupt_kernel"):
                await parent.interrupt_kernel()
        except Exception as exc:  # noqa: BLE001 — best effort
            logger.warning(f"| ⚠️ Could not interrupt the kernel: {exc}")


def _strip_ansi(text: str) -> str:
    """Tracebacks arrive colourized; the colour codes are noise in a transcript."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


# Global kernel manager instance
kernel_manager = KernelManagerServer()

__all__ = ["KernelManagerServer", "kernel_manager"]
