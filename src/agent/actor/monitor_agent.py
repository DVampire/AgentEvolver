"""MonitorAgent — workflow agent that launches a bash subprocess and polls it periodically.

Design
------
- Overrides ``on_start`` and returns ``None`` (async resolution).
- Spawns two background asyncio tasks:
    1. ``_drain_output``  — continuously reads stdout to prevent pipe back-pressure.
    2. ``_monitor_loop`` — uses ``asyncio.wait_for(process.wait(), timeout=poll_interval)``
       so process completion is detected immediately, and poll timeouts trigger progress reports.
- On each poll timeout sends a ``MonitorProgressMessage`` to the parent AgentRef inbox
  (obtained from ``ctx.extra["parent_ref"]`` injected by MetaAgent._run_subtask).
- On process completion resolves ``ref._pending_reply`` with the full output.
- On ``max_wait`` exceeded kills the process and raises TimeoutError.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.agent.actor.meta_agent import MonitorProgressMessage
from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse
from src.logger import logger
from src.registry import AGENT
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class MonitorAgent(Agent):
    """Launches a bash command and monitors it, sending periodic progress reports to the parent agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="monitor_agent")
    description: str = Field(
        default=(
            "Starts a long-running bash process and monitors it, "
            "reporting progress to the parent agent at regular intervals."
        )
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=False)

    poll_interval: int = Field(default=30, description="Seconds between progress reports.")
    max_wait: int = Field(default=3600, description="Maximum seconds to wait before killing the process.")
    tail_lines: int = Field(default=50, description="Lines of recent stdout to include in each progress report.")

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        poll_interval: int = 30,
        max_wait: int = 3600,
        tail_lines: int = 50,
        require_grad: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            require_grad=require_grad,
            **kwargs,
        )
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.tail_lines = tail_lines

    # ------------------------------------------------------------------
    # Lifecycle entry point — overrides on_start for async resolution
    # ------------------------------------------------------------------

    async def on_start(
        self,
        task: str,
        files: Optional[List[str]],
        ctx: Optional[AgentContext],
        ref: Any,
        **kwargs: Any,
    ) -> Optional[AgentResponse]:
        command = kwargs.get("command") or task
        parent_ref = ctx.extra.get("parent_ref") if ctx else None
        task_id = (ctx.task_id if ctx else None) or make_id()

        logger.info(f"| 🚀 MonitorAgent [{task_id}]: starting process")
        logger.info(f"|    command: {command[:300]}")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            logger.error(f"| ❌ MonitorAgent [{task_id}]: failed to spawn process: {exc}")
            return AgentResponse(message=f"Failed to start process: {exc}", success=False)

        logger.info(f"| 🔵 MonitorAgent [{task_id}]: pid={process.pid}")

        output_buffer: List[str] = []
        asyncio.create_task(self._drain_output(process.stdout, output_buffer))
        asyncio.create_task(
            self._monitor_loop(process, output_buffer, task_id, ctx, ref, parent_ref)
        )
        return None  # Async resolution — _monitor_loop will set ref._pending_reply

    # ------------------------------------------------------------------
    # Background helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _drain_output(
        stdout: asyncio.StreamReader,
        buffer: List[str],
    ) -> None:
        """Read stdout line-by-line into a rolling buffer to prevent pipe back-pressure."""
        async for raw in stdout:
            line = raw.decode(errors="replace")
            buffer.append(line)
            if len(buffer) > 1000:
                buffer.pop(0)

    async def _monitor_loop(
        self,
        process: asyncio.subprocess.Process,
        output_buffer: List[str],
        task_id: str,
        ctx: Optional[AgentContext],
        ref: Any,
        parent_ref: Any,
    ) -> None:
        loop = asyncio.get_event_loop()
        start = loop.time()
        session_id = (ctx.id if ctx else None) or task_id

        try:
            while True:
                elapsed = loop.time() - start
                remaining = self.max_wait - elapsed

                if remaining <= 0:
                    process.kill()
                    await process.wait()
                    logger.warning(
                        f"| ⏰ MonitorAgent [{task_id}]: timed out after {elapsed:.0f}s, process killed"
                    )
                    _safe_set_exception(
                        ref,
                        TimeoutError(f"Process (pid={process.pid}) timed out after {self.max_wait}s"),
                    )
                    if parent_ref is not None:
                        await parent_ref._inbox.put(MonitorProgressMessage(
                            task_id=task_id, agent_name=self.name, session_id=session_id,
                            pid=process.pid, status="timeout", elapsed=elapsed,
                        ))
                    return

                try:
                    # Wait for process exit OR poll_interval — whichever comes first.
                    await asyncio.wait_for(
                        asyncio.shield(process.wait()),
                        timeout=min(self.poll_interval, remaining),
                    )

                    # --- Process finished ---
                    elapsed = loop.time() - start
                    output = "".join(output_buffer)
                    exit_code = process.returncode
                    success = exit_code == 0

                    logger.info(
                        f"| {'✅' if success else '❌'} MonitorAgent [{task_id}]: "
                        f"exited code={exit_code} elapsed={elapsed:.0f}s"
                    )

                    result = AgentResponse(
                        message=output[-4000:] if output else f"Process exited with code {exit_code}.",
                        success=success,
                        extra=AgentExtra(data={
                            "exit_code": exit_code,
                            "elapsed_seconds": round(elapsed, 1),
                            "pid": process.pid,
                        }),
                    )
                    _safe_set_result(ref, result)

                    if parent_ref is not None:
                        await parent_ref._inbox.put(MonitorProgressMessage(
                            task_id=task_id, agent_name=self.name, session_id=session_id,
                            pid=process.pid, status="completed", elapsed=elapsed,
                            recent_output=output[-2000:], exit_code=exit_code,
                        ))
                    return

                except asyncio.TimeoutError:
                    # --- Still running — send progress report ---
                    elapsed = loop.time() - start
                    snapshot = "".join(output_buffer[-self.tail_lines:])
                    logger.info(
                        f"| 📡 MonitorAgent [{task_id}]: running, "
                        f"elapsed={elapsed:.0f}s pid={process.pid}"
                    )
                    if parent_ref is not None:
                        await parent_ref._inbox.put(MonitorProgressMessage(
                            task_id=task_id, agent_name=self.name, session_id=session_id,
                            pid=process.pid, status="running", elapsed=elapsed,
                            recent_output=snapshot,
                        ))

        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            logger.info(f"| ✋ MonitorAgent [{task_id}]: cancelled, process killed")
            raise

        except Exception as exc:
            logger.error(f"| ❌ MonitorAgent [{task_id}]: unexpected error: {exc}", exc_info=True)
            _safe_set_exception(ref, exc)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _safe_set_result(ref: Any, result: Any) -> None:
    future = getattr(ref, "_pending_reply", None)
    if future is not None and not future.done():
        future.set_result(result)


def _safe_set_exception(ref: Any, exc: Exception) -> None:
    future = getattr(ref, "_pending_reply", None)
    if future is not None and not future.done():
        future.set_exception(exc)
