"""RuntimeManager — spawn / send / ask / stop / invoke / list / shutdown.

The runtime manages **running** agent refs. Class registration / versioning
is still owned by agent_manager; this module only tracks who is alive.

Typical use (for long-lived sessions):
    ref = await runtime_manager.spawn("code_agent")
    await runtime_manager.send(ref, TaskMessage(task="..."))
    await runtime_manager.stop(ref)

One-shot sugar (used internally by Agent.__call__):
    result = await runtime_manager.invoke("code_agent", task="...", ctx=...)
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Union

from src.logger import logger
from src.utils import Singleton, make_id
from src.runtime.pump import _pump
from src.runtime.types import (
    AgentDeadError,
    AgentRef,
    AgentStatus,
    BaseMessage,
    StopMessage,
    TaskMessage,
    _current_ref_var,
)


class RuntimeManager(metaclass=Singleton):
    """Singleton holding all running AgentRefs."""

    def __init__(self) -> None:
        self._refs: Dict[str, AgentRef] = {}

    # ------------------------------------------------------------------
    # Spawn / stop lifecycle
    # ------------------------------------------------------------------

    async def spawn(
        self,
        agent_or_name: Union[str, Any],
        *,
        name: Optional[str] = None,
        parent_ref: Optional[AgentRef] = None,
    ) -> AgentRef:
        """Start a pump for one agent and register the ref.

        agent_or_name : registered agent name (str) or Agent instance.
        name          : optional explicit ref name; defaults to "<agent>-<id>".
        parent_ref    : optional parent ref for hierarchical wiring.
        """
        if isinstance(agent_or_name, str):
            from src.agent.server import agent_manager
            agent = await agent_manager.get(agent_or_name)
            if agent is None:
                raise ValueError(f"No registered agent named {agent_or_name!r}")
        else:
            agent = agent_or_name

        agent_name = getattr(agent, "name", agent.__class__.__name__)
        ref_name = name or f"{agent_name}-{make_id()}"
        existing = self._refs.get(ref_name)
        if existing is not None and existing.status == AgentStatus.RUNNING:
            raise ValueError(f"AgentRef name collision: {ref_name!r} is already RUNNING")

        ref = AgentRef(name=ref_name, agent_name=agent_name, status=AgentStatus.RUNNING)
        ref._parent_ref = parent_ref
        ref._pump_task = asyncio.create_task(_pump(agent, ref), name=f"pump-{ref_name}")

        self._refs[ref_name] = ref
        logger.info(f"| 🟢 Runtime spawned: {ref}")
        return ref

    async def stop(
        self,
        ref: AgentRef,
        *,
        drain: bool = True,
        timeout: Optional[float] = None,
        reason: str = "manual",
    ) -> None:
        """Stop the ref.

        drain=True : enqueue StopMessage, wait for pump to finish current task and exit.
        drain=False: cancel pump immediately (in-flight task gets CancelledError).
        """
        if ref.status != AgentStatus.RUNNING:
            self._refs.pop(ref.name, None)
            return

        ref.status = AgentStatus.STOPPING
        try:
            if drain:
                await ref._inbox.put(StopMessage(reason=reason))
                if ref._pump_task is not None:
                    if timeout is not None:
                        try:
                            await asyncio.wait_for(asyncio.shield(ref._pump_task), timeout=timeout)
                        except asyncio.TimeoutError:
                            logger.warning(f"| ⏱ stop(drain) timeout: {ref.name} — cancelling pump")
                            ref._pump_task.cancel()
                            await asyncio.gather(ref._pump_task, return_exceptions=True)
                    else:
                        await ref._pump_task
            else:
                if ref._pump_task is not None and not ref._pump_task.done():
                    ref._pump_task.cancel()
                    await asyncio.gather(ref._pump_task, return_exceptions=True)
        finally:
            if ref.status != AgentStatus.DEAD:
                ref.status = AgentStatus.STOPPED
            self._refs.pop(ref.name, None)
            logger.info(f"| ⚫ Runtime stopped: {ref}")

    async def shutdown(self) -> None:
        """Stop every running ref. Call on process shutdown."""
        for ref in list(self._refs.values()):
            await self.stop(ref, drain=False, reason="shutdown")

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(self, ref: AgentRef, msg: BaseMessage) -> None:
        """Fire-and-forget."""
        if ref.status != AgentStatus.RUNNING:
            raise AgentDeadError(f"Cannot send to {ref}: not RUNNING")
        await ref._inbox.put(msg)

    async def ask(
        self,
        ref: AgentRef,
        msg: BaseMessage,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send and await reply on msg.reply_future."""
        if msg.reply_future is None:
            msg.reply_future = asyncio.get_event_loop().create_future()
        await self.send(ref, msg)
        if timeout is not None:
            return await asyncio.wait_for(msg.reply_future, timeout=timeout)
        return await msg.reply_future

    async def invoke(
        self,
        agent_or_name: Union[str, Any],
        *,
        name: Optional[str] = None,
        parent_ref: Optional[AgentRef] = None,
        timeout: Optional[float] = None,
        **task_kwargs: Any,
    ) -> Any:
        """One-shot: spawn + ask(TaskMessage) + stop. Returns agent's result.

        ``name`` / ``parent_ref`` are forwarded to ``spawn``; everything else
        is forwarded into the TaskMessage as agent kwargs. Used by
        ``Agent.__call__`` so every direct invocation also gets a runtime ref.
        """
        ref = await self.spawn(agent_or_name, name=name, parent_ref=parent_ref)
        try:
            task = task_kwargs.pop("task", None)
            msg = TaskMessage(task=task, kwargs=task_kwargs)
            return await self.ask(ref, msg, timeout=timeout)
        finally:
            # drain=False because the agent has already returned by the time
            # ask resolves — there is no more work to drain.
            await self.stop(ref, drain=False)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[AgentRef]:
        return self._refs.get(name)

    def list(self) -> List[AgentRef]:
        return list(self._refs.values())

    def current_ref(self) -> Optional[AgentRef]:
        """The AgentRef whose pump is driving the calling asyncio task.

        Returns None if called outside any pump (e.g., from top-level code
        before any spawn). Inherited by child tasks via contextvar copy.
        """
        return _current_ref_var.get()


runtime_manager = RuntimeManager()
