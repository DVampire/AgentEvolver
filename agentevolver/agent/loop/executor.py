"""Run one turn's actions.

Read-only batches run together; anything else runs in order and stops at the first
failure, because the calls after it were planned without seeing that result. Unknown is
treated as effectful — a classifier that guesses the other way reorders side effects the
one time it is wrong, and the failure is invisible.

This is the whole of ``act``. It has no opinion about what a capability is, no hooks, and
no access to the agent's state; it takes calls and returns results. The previous design
posted each result back through the agent's own mailbox to be reassembled by three more
methods, which is the same work spread across a scheduler.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any, Dict, List, Optional, Sequence

from agentevolver.agent.loop.decision import ActionCall, ActionResult
from agentevolver.agent.loop.events import events
from agentevolver.agent.loop.router import ToolRouter
from agentevolver.code import BATCH_CALL_TOOL, GuardedDispatch
from agentevolver.logger import logger


class ActionExecutor:
    """Executes a turn's batch through a router."""

    def __init__(self, router: ToolRouter) -> None:
        self.router = router

    async def consume(self, stream: Any, *, agent: Any, ctx: Any,
                      routing: Dict[str, Any], eligible: set[str],
                      execution: Optional[Dict[str, Any]] = None
                      ) -> tuple[Dict[str, Any], Dict[str, ActionResult]]:
        """Overlap complete native async reads with generation, closing jobs on exit.

        Pending calls never enter durable context. No speculative writes, execution of
        argument fragments, or automatic replay after a stream disconnect.
        """
        from agentevolver.model.types import ToolCallComplete, accumulate_stream

        jobs: Dict[str, asyncio.Task] = {}
        seen: Dict[str, ActionCall] = {}

        async def watched():
            barrier = False
            async for event in stream:
                if isinstance(event, ToolCallComplete):
                    call = ActionCall(event.id, event.name, event.input, event.caller)
                    if not call.id or call.id in seen:
                        raise ValueError("Stream contains an empty or duplicate tool-call id")
                    seen[call.id] = call
                    safe = (isinstance(call.args, dict) and call.name in eligible and not call.caller
                            and "__raw__" not in call.args
                            and self.router.read_only(call, routing) is True)
                    # A read after a write may depend on that write's result.
                    barrier = barrier or not safe
                    if (event.asynchronous and safe and not barrier
                            and len(seen) <= agent.max_actions):
                        jobs[call.id] = asyncio.create_task(self._one(
                            call, agent, ctx, routing, execution, len(seen) - 1,
                        ))
                yield event

        try:
            accumulated = await accumulate_stream(watched())
            if accumulated.get("stop_reason") not in (None, "end_turn", "tool_use"):
                return accumulated, {}
            # The adapter must not change a completed call in a later terminal item.
            for call in accumulated["tool_calls"]:
                prior = seen.get(call.id)
                if prior and (prior.name != call.name or prior.args != call.input):
                    raise ValueError("Completed tool call changed within a response")
            results = await asyncio.gather(*jobs.values())
            return accumulated, dict(zip(jobs, results))
        finally:
            for job in jobs.values():
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs.values(), return_exceptions=True)
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def run(
        self,
        calls: Sequence[ActionCall],
        *,
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Optional[Dict[str, Any]] = None,
        prepared: Optional[Dict[str, ActionResult]] = None,
    ) -> List[ActionResult]:
        """Run every call, in parallel when that is provably safe.

        Always returns one result per call, in the order the calls were given, so the
        conversation's tool results line up with the assistant turn that asked for them
        even when execution stopped early.
        """
        if not calls:
            return []
        prepared = prepared or {}
        if any(call.id in prepared and prepared[call.id].call != call for call in calls):
            raise ValueError("Prepared result does not match the completed tool call")
        if self._parallel_safe(calls, routing):
            logger.info(f"| ⚡ {len(calls)} read-only action(s) in parallel")
            return list(
                await asyncio.gather(
                    *[
                        self._result(call, agent, ctx, routing, execution, index, prepared)
                        for index, call in enumerate(calls)
                    ]
                )
            )
        return await self._serial(calls, agent, ctx, routing, execution, prepared)

    async def _result(self, call, agent, ctx, routing, execution, index, prepared):
        if call.id in prepared:
            return prepared[call.id]
        return await self._one(call, agent, ctx, routing, execution, index)

    async def _serial(
        self,
        calls: Sequence[ActionCall],
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Optional[Dict[str, Any]],
        prepared: Dict[str, ActionResult],
    ) -> List[ActionResult]:
        results: List[ActionResult] = []
        for index, call in enumerate(calls):
            result = await self._result(call, agent, ctx, routing, execution, index, prepared)
            results.append(result)
            if result.error:
                skipped = [item.name for item in calls[index + 1:] if item.id not in prepared]
                if skipped:
                    logger.warning(
                        f"| ⏹️ batch stopped after {call.name} failed; "
                        f"skipped: {skipped}"
                    )
                # A skipped call still needs a result, or the assistant turn is left
                # with unanswered tool calls and the whole conversation is unsendable.
                results.extend(
                    prepared[item.id] if item.id in prepared else ActionResult(
                        call=item,
                        error=(
                            f"Not executed: the batch stopped after {call.name!r} "
                            "failed. Resolve that first, then retry."
                        ),
                    )
                    for item in calls[index + 1:]
                )
                break
        return results

    async def _one(
        self,
        call: ActionCall,
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Optional[Dict[str, Any]],
        index: int,
    ) -> ActionResult:
        from agentevolver.hook.types import HookDecision, HookEvent

        coordinates = dict(execution or {})
        coordinates.update({"call_id": call.id, "action_index": index})
        action = {
            "index": index, "id": call.id, "name": call.name,
            "type": (routing.get(call.name) or ("tool",))[0],
            "args_parsed": call.args, "description": "",
        }
        body = {**coordinates, "action": action}

        # The gates. A refusal is data, not an exception: it travels back as an ordinary
        # result so the assistant turn it answers stays complete and the model reads an
        # actionable reason instead of finding a call it made simply missing.
        denial = self.router.denial(call, routing, agent)
        if not denial:
            verdict = await events.gate("plan_mode_hook", {
                "event": HookEvent.PRE_ACTION, **body,
            }, ctx=ctx)
            if getattr(verdict, "decision", None) is HookDecision.BLOCK:
                denial = str(getattr(verdict, "reason", "") or "Blocked by plan mode.")
        if denial:
            logger.warning(f"| 🚫 {call.name}: {denial}")
            coordinates["guard_denials"] = [denial]

        await events.emit(HookEvent.PRE_ACTION, body, ctx=ctx)
        logger.info(f"| 📝 {call.name}({_brief(call.args)})")
        # Passed only when there is one. A router with no program transport to serve
        # never sees the argument, so a simpler implementation stays valid instead of
        # failing on an unexpected keyword.
        bridge = self._bridge(call, agent, ctx, routing, coordinates)
        extra = {"bridge": bridge} if bridge is not None else {}
        try:
            result = await self.router.invoke(
                call, agent=agent, ctx=ctx, routing=routing, execution=coordinates,
                **extra,
            )
        except Exception as error:  # noqa: BLE001 - a router fault is still a result
            logger.error(f"| ❌ {call.name} failed: {error}")
            result = ActionResult(call=call, error=f"{type(error).__name__}: {error}")
        if denial and result.ok:
            # A capability kind with no execution pipeline of its own never saw the
            # denial; settle it here so a refused action can never take effect.
            result = ActionResult(call=call, error=denial)
        await events.emit(HookEvent.POST_ACTION, {
            **body,
            "action_result": result.output,
            "error": result.error or None,
        }, ctx=ctx)
        return result

    def _bridge(
        self,
        call: ActionCall,
        agent: Any,
        ctx: Any,
        routing: Dict[str, Any],
        execution: Dict[str, Any],
    ) -> Optional[GuardedDispatch]:
        """A way for a program to call one tool, bound to this turn.

        The program transport is the only call handed a dispatcher, and the dispatcher it
        gets re-enters :meth:`_one`. That is the whole safety argument for running a
        program: every check a call passes lives here and in the tool itself, so a second
        dispatcher — however careful — would be a second copy of the plan-mode gate, the
        read-only refusal and the hook pair, drifting quietly from this one.

        Only ``tool`` routes are bound. The model is told what it may call by a
        declaration generated from the tool roster, so binding names it was never shown
        buys nothing.
        """
        route = routing.get(call.name) or ()
        if not route or route[0] != "tool" or route[1] != BATCH_CALL_TOOL:
            return None
        from agentevolver.tool.default.execution.sdk import callable_names

        names = tuple(callable_names([
            name for name, item in routing.items()
            if (item or ("tool",))[0] == "tool"
        ]))
        served = itertools.count(1)
        parent_call_id = str(call.id)

        async def dispatch(name: str, args: Dict[str, Any]) -> str:
            if name not in names:
                raise LookupError(
                    f"'{name}' is not callable from a program here. Callable: "
                    f"{', '.join(names) or 'none'}."
                )
            # A distinct id per sub-call, derived from the program's own, so the trace
            # shows which program each action came out of rather than a flat run of
            # unattributed calls.
            sub = ActionCall(
                id=f"{parent_call_id}#{next(served)}", name=name, args=dict(args or {}),
            )
            coordinates = {**execution, "parent_call_id": parent_call_id}
            result = await self._one(sub, agent, ctx, routing, coordinates, 0)
            if result.error:
                raise RuntimeError(result.error)
            if not result.output:
                # A gate refused it. Returning an empty success would tell the program
                # the tool did its work and had nothing to say.
                raise RuntimeError(f"'{name}' was blocked before it ran.")
            return str(result.output)

        return GuardedDispatch(names=names, call=dispatch)

    def _parallel_safe(
        self, calls: Sequence[ActionCall], routing: Dict[str, Any]
    ) -> bool:
        """Only when every call in the batch is declared read-only."""
        if len(calls) < 2:
            return False
        return all(self.router.read_only(call, routing) is True for call in calls)


def _brief(args: Dict[str, Any], limit: int = 120) -> str:
    """A short, single-line rendering of call arguments for the log."""
    text = ", ".join(f"{key}={value!r}" for key, value in sorted(args.items()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["ActionExecutor"]
