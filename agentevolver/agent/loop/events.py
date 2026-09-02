"""One place that turns a fact into notifications, and one that asks a policy.

The previous design had neither, and paid for it twice. Observers were called by name —
``trace_hook`` and ``trajectory_hook`` appeared side by side at every one of twenty-four
call sites — so the loop had to know which observers existed, and adding one meant
editing the agent. And observation and policy were the same call shape, so a step
waited on a trace write exactly as it waited on a budget check.

They are separated here:

``emit``   a fact happened. Every observer hears it, none of them can refuse it, and one
           that fails is logged rather than propagated. Callers do not name observers.
``gate``   a policy decision. Exactly one named hook, awaited, and its verdict is
           binding. Used for the budget check and the plan-mode gate.

Where each is raised is decided by who knows the fact: the kernel owns process
lifecycle, the loop owns steps, the executor owns actions, and the tool pipeline owns
everything inside a tool call.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from agentevolver.logger import logger

#: Observers every lifecycle fact is offered to. They subscribe by being listed here
#: rather than by each call site naming them, which is the point.
OBSERVERS: Tuple[str, ...] = ("trace_hook", "trajectory_hook")


class EventBus:
    """Broadcasts facts to observers; asks policy hooks one at a time."""

    def __init__(self, observers: Sequence[str] = OBSERVERS) -> None:
        self.observers = tuple(observers)

    async def emit(
        self, event: Any, payload: Optional[Dict[str, Any]] = None, *, ctx: Any = None
    ) -> None:
        """Tell every observer that something happened.

        Never raises and never returns a verdict. An observer that cannot write must not
        be able to stop a run — that is the whole difference between watching a run and
        governing one.
        """
        body = {"event": event, **(payload or {})}
        for name in self.observers:
            try:
                await self._call(name, body, ctx)
            except Exception as error:  # noqa: BLE001 - observation is never fatal
                logger.warning(f"| ⚠️ observer {name} failed on {event}: {error}")

    async def gate(
        self, hook: str, payload: Optional[Dict[str, Any]], *, ctx: Any = None
    ) -> Any:
        """Ask one policy hook for a verdict. Its answer is binding.

        A hook that raises is treated as ALLOW: a broken budget checker must not become
        a run that cannot act, which is the failure mode of making policy fail closed by
        accident rather than by declaration.
        """
        from agentevolver.hook.types import HookResult

        try:
            return await self._call(hook, dict(payload or {}), ctx)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"| ⚠️ policy hook {hook} failed: {error}")
            return HookResult.allow()

    async def broadcast(
        self, event: Any, payload: Optional[Dict[str, Any]] = None, *, ctx: Any = None
    ) -> None:
        """Publish to whoever subscribed, by event rather than by name.

        The hook registry supports both shapes. ``emit`` above calls the named observers
        that expect a payload with an ``event`` key; this one reaches hooks that
        subscribed to a lifecycle event — the session and promotion hooks — without
        anyone naming them. Process lifecycle uses this.
        """
        from agentevolver.hook.server import hook_manager

        try:
            await hook_manager.emit(event, dict(payload or {}), ctx=ctx)
        except Exception as error:  # noqa: BLE001 - observation is never fatal
            logger.warning(f"| ⚠️ broadcast of {event} failed: {error}")

    @staticmethod
    async def _call(name: str, body: Dict[str, Any], ctx: Any) -> Any:
        from agentevolver.hook.server import hook_manager

        return await hook_manager(name=name, input=body, ctx=ctx)


#: Shared bus. One per process, like the hook registry it fans out to.
events = EventBus()


__all__ = ["OBSERVERS", "EventBus", "events"]
