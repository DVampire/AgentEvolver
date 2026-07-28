"""Execute a notebook cell in the workstation's kernel.

The science container already runs a Jupyter Server — that is what serves the
Lab — so the kernel is reached through its REST + WebSocket API rather than by
starting a second one. A cell run from our own UI and a cell run from the
embedded Lab therefore land in the *same* kernel: the variables one defines,
the other sees.

Outputs come back as :class:`agentevolver.kernel.KernelResult`, the same model
``code_interpreter_tool`` returns. One shape for "what a cell produced", so a
notebook cell, a tool call and a transcript all render from the same fields.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from agentevolver.kernel.types import KernelOutput, KernelResult
from agentevolver.logger import logger

#: Jupyter's wire protocol version for the messages we send.
_PROTOCOL = "5.3"
#: Give up on a cell after this long and interrupt the kernel.
DEFAULT_TIMEOUT = 600.0

#: Callback invoked with each output as it arrives, so a long cell streams
#: instead of appearing all at once when it finishes.
OnOutput = Callable[[KernelOutput], Awaitable[None]]


def _message(msg_type: str, content: Dict[str, Any], session: str) -> Dict[str, Any]:
    """One Jupyter wire message, JSON-shaped for the WebSocket channel."""
    return {
        "header": {
            "msg_id": uuid.uuid4().hex,
            "username": "agentevolver",
            "session": session,
            "date": datetime.now(timezone.utc).isoformat(),
            "msg_type": msg_type,
            "version": _PROTOCOL,
        },
        "parent_header": {},
        "metadata": {},
        "content": content,
        "channel": "shell",
    }


class NotebookKernel:
    """One kernel in the workstation, bound to one notebook path.

    Bound to the *path* rather than to a client: two browser tabs on the same
    notebook share a kernel, which is what makes the notebook's state feel like
    the notebook's rather than the tab's.
    """

    def __init__(self, base_url: str, path: str, *, kernel_name: str = "python3") -> None:
        #: e.g. ``http://127.0.0.1:41293/science/<session>``
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.kernel_name = kernel_name
        self.kernel_id: Optional[str] = None
        self.session_id: str = uuid.uuid4().hex
        self._lock = asyncio.Lock()

    async def ensure(self) -> str:
        """The kernel serving this notebook, asking the server to start one if needed.

        A Jupyter *session* rather than a bare kernel, so the server associates
        the kernel with the notebook's path — which is what makes the embedded
        Lab attach to this same kernel when the file is opened there.
        """
        if self.kernel_id:
            return self.kernel_id
        import aiohttp

        async with aiohttp.ClientSession() as http:
            async with http.get(f"{self.base_url}/api/sessions", timeout=aiohttp.ClientTimeout(total=30)) as response:
                for existing in await response.json():
                    if existing.get("path") == self.path and existing.get("kernel", {}).get("id"):
                        self.kernel_id = existing["kernel"]["id"]
                        return self.kernel_id

            payload = {"path": self.path, "type": "notebook",
                       "name": self.path.rsplit("/", 1)[-1],
                       "kernel": {"name": self.kernel_name}}
            async with http.post(f"{self.base_url}/api/sessions", json=payload,
                                 timeout=aiohttp.ClientTimeout(total=120)) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Jupyter refused a session for {self.path}: "
                                       f"{response.status} {await response.text()}")
                body = await response.json()
        self.kernel_id = body["kernel"]["id"]
        logger.info(f"| 🔬 Kernel {self.kernel_id[:8]} attached to {self.path}")
        return self.kernel_id

    async def execute(self, code: str, *, timeout: float = DEFAULT_TIMEOUT,
                      on_output: Optional[OnOutput] = None) -> KernelResult:
        """Run one cell and collect everything it produced.

        Serialized per notebook: a kernel executes one cell at a time, and two
        concurrent callers would otherwise interleave their iopub messages —
        the same reason the in-process kernel manager holds a lock per project.
        """
        async with self._lock:
            try:
                return await self._execute(code, timeout, on_output)
            except Exception as exc:  # noqa: BLE001 — a dead kernel is a failed cell
                logger.warning(f"| ⚠️ Cell failed on {self.path}: {exc}")
                return KernelResult(success=False, error=str(exc))

    async def _execute(self, code: str, timeout: float, on_output: Optional[OnOutput]) -> KernelResult:
        import aiohttp

        kernel_id = await self.ensure()
        ws_url = f"{self.base_url}/api/kernels/{kernel_id}/channels?session_id={self.session_id}"
        result = KernelResult()
        outputs = []

        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(ws_url, heartbeat=30,
                                       timeout=aiohttp.ClientTimeout(total=60)) as socket:
                request = _message("execute_request", {
                    "code": code, "silent": False, "store_history": True,
                    "user_expressions": {}, "allow_stdin": False, "stop_on_error": True,
                }, self.session_id)
                await socket.send_json(request)
                msg_id = request["header"]["msg_id"]

                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        await self.interrupt()
                        result.success = False
                        result.error = f"Execution exceeded {timeout:.0f}s and was interrupted."
                        break
                    try:
                        raw = await asyncio.wait_for(socket.receive(), timeout=remaining)
                    except asyncio.TimeoutError:
                        continue
                    if raw.type is not aiohttp.WSMsgType.TEXT:
                        break  # the socket closed under us
                    message = json.loads(raw.data)
                    # Messages from an earlier cell can still be draining; only
                    # this execution's replies describe this execution.
                    if (message.get("parent_header") or {}).get("msg_id") != msg_id:
                        continue
                    if message.get("channel") != "iopub":
                        continue

                    output, done = self._interpret(message, result)
                    if output is not None:
                        outputs.append(output)
                        if on_output is not None:
                            await on_output(output)
                    if done:
                        break

        result.outputs = outputs
        return result

    @staticmethod
    def _interpret(message: Dict[str, Any], result: KernelResult) -> tuple[Optional[KernelOutput], bool]:
        """Turn one iopub message into an output, and say whether the cell is done."""
        msg_type, content = message.get("msg_type"), message.get("content") or {}
        if msg_type == "stream":
            return KernelOutput(type="stream", name=content.get("name"),
                                data={"text/plain": content.get("text", "")}), False
        if msg_type in ("execute_result", "display_data", "update_display_data"):
            if msg_type == "execute_result":
                result.execution_count = content.get("execution_count")
            return KernelOutput(type="result" if msg_type == "execute_result" else "display",
                                data=dict(content.get("data") or {})), False
        if msg_type == "error":
            traceback = "\n".join(content.get("traceback") or [])
            result.success = False
            result.error = _strip_ansi(traceback) or f"{content.get('ename')}: {content.get('evalue')}"
            return KernelOutput(type="error", data={"text/plain": result.error}), False
        if msg_type == "status" and content.get("execution_state") == "idle":
            return None, True
        return None, False

    async def interrupt(self) -> bool:
        """Stop whatever the kernel is doing. True if there was a kernel to stop."""
        if not self.kernel_id:
            return False
        import aiohttp

        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.base_url}/api/kernels/{self.kernel_id}/interrupt",
                                 timeout=aiohttp.ClientTimeout(total=30)) as response:
                return response.status < 400

    async def restart(self) -> bool:
        """Throw the kernel's state away and start fresh."""
        if not self.kernel_id:
            return False
        import aiohttp

        async with aiohttp.ClientSession() as http:
            async with http.post(f"{self.base_url}/api/kernels/{self.kernel_id}/restart",
                                 timeout=aiohttp.ClientTimeout(total=60)) as response:
                return response.status < 400


def _strip_ansi(text: str) -> str:
    """Tracebacks arrive colourized; the colour codes are noise in the UI."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


__all__ = ["NotebookKernel", "DEFAULT_TIMEOUT"]
