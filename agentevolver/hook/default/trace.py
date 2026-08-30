"""TraceHook — probe middleware that emits TraceEvents from agent lifecycle hooks."""

from __future__ import annotations

import json
import time
from typing import Dict, Optional

from pydantic import PrivateAttr

from agentevolver.hook.types import Hook, HookContext, HookEvent, HookResult
from agentevolver.registry import HOOK
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_start_event,
    agent_call_event,
    agent_end_event,
    tool_start_event,
    tool_call_event,
    skill_start_event,
    skill_call_event,
)


@HOOK.register_module(force=True)
class TraceHook(Hook):
    """Fire-and-forget probe: translates HookEvents → TraceEvents."""

    name: str = "trace_hook"
    description: str = "Emits structured TraceEvents for every agent lifecycle hook."
    events: list = []
    priority: int = 1

    _timers: Dict[str, float] = PrivateAttr(default_factory=dict)
    #: session → running total of every step's usage, so the run's cost is recorded as a
    #: figure and not left to be re-derived by summing the log. A reader that has to do
    #: that arithmetic cannot tell a session whose steps were partly unrecorded from one
    #: that genuinely cost that little.
    _usage: Dict[str, Dict[str, float]] = PrivateAttr(default_factory=dict)

    async def handle(self, ctx: HookContext) -> HookResult:
        """Translate the incoming agent lifecycle event into a TraceEvent and emit it.

        Dispatches on ``ctx.input["event"]``: ON_START/ON_STOP bracket the agent
        run, PRE_STEP/POST_STEP the step, PRE_ACTION/POST_ACTION each tool or skill
        action. Start events stash a monotonic timer keyed by session/step/action;
        the matching end event pops it to compute ``duration_ms``. Any resulting
        event is emitted through ``trace_manager``.

        Args:
            ctx: Hook context whose ``id`` is the session id and whose ``input``
                carries the event type and its payload (agent_name, step, action).

        Returns:
            Always ``HookResult.allow()`` (this hook only observes).
        """
        from agentevolver.trace.server import trace_manager

        inp = ctx.input
        if inp is None:
            return HookResult.allow()

        event: Optional[TraceEvent] = None
        agent_name = inp.get("agent_name")
        inp_event = inp.get("event")

        if inp_event == HookEvent.ON_START:
            event = agent_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                task_content=self._task_content(ctx),
            )
            parent_session_id = inp.get("parent_session_id")
            if parent_session_id:
                event = event.model_copy(update={"metadata": {
                    **event.metadata,
                    "parent_session_id": parent_session_id,
                    "subtask_id": inp.get("subtask_id") or "",
                }})
            self._timers[f"{ctx.id}:agent"] = time.monotonic()

        elif inp_event == HookEvent.ON_STOP:
            elapsed = self._pop_timer(f"{ctx.id}:agent")
            # The outcome the run reported, not a constant. This was hardcoded to
            # `success=True, result=None`, so every agent_end claimed success — including
            # a run that gave up after three consecutive model errors. A measurement
            # taken from that log counted a failed run as a valid sample, which is how a
            # broken `derive_context` path was measured and reported as working.
            result = inp.get("result")
            event = agent_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                success=bool(inp.get("success", False)),
                result=str(result) if result is not None else None,
                duration_ms=elapsed,
                error=inp.get("error"),
                usage=self._usage.pop(ctx.id, None),
            )

        elif inp_event == HookEvent.PRE_STEP:
            step = inp.get("step_number")
            self._timers[f"{ctx.id}:step:{step}"] = time.monotonic()

        elif inp_event == HookEvent.POST_STEP:
            step = inp.get("step_number")
            elapsed = self._pop_timer(f"{ctx.id}:step:{step}")
            step_usage = inp.get("step_usage")
            event = agent_call_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                reasoning=inp.get("reasoning"),
                assistant_text=inp.get("assistant_text"),
                provider_state=inp.get("provider_state"),
                duration_ms=elapsed,
                usage=step_usage,
            )
            self._add_usage(ctx.id, step_usage)

        elif inp_event == HookEvent.PRE_ACTION:
            action = inp.get("action") or {}
            step = inp.get("step_number")
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            aargs = action.get("args_parsed", action.get("args", {}))
            if isinstance(aargs, str):
                try:
                    aargs = json.loads(aargs)
                except Exception:
                    aargs = {"raw": aargs}
            factory = tool_start_event if atype == "tool" else skill_start_event
            event = factory(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                action_index=idx,
                action_name=aname,
                action_args=aargs,
                call_id=str(action.get("id") or ""),
            )
            if action.get("parent_call_id"):
                event.metadata["parent_call_id"] = str(action["parent_call_id"])
            self._timers[f"{ctx.id}:action:{step}:{idx}"] = time.monotonic()

        elif inp_event == HookEvent.POST_ACTION:
            action = inp.get("action") or {}
            step = inp.get("step_number")
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            success = not bool(inp.get("error"))
            error = inp.get("error")
            elapsed = self._pop_timer(f"{ctx.id}:action:{step}:{idx}")
            factory = tool_call_event if atype == "tool" else skill_call_event
            event = factory(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                action_index=idx,
                action_name=aname,
                result=inp.get("action_result"),
                success=success,
                duration_ms=elapsed,
                error=error,
                call_id=str(action.get("id") or ""),
            )
            if action.get("parent_call_id"):
                event.metadata["parent_call_id"] = str(action["parent_call_id"])
            execution_meta = inp.get("execution_meta")
            if isinstance(execution_meta, dict) and execution_meta:
                # The Tool pipeline owns this classified outcome. Keep it nested so its
                # versioned vocabulary cannot collide with generic Trace metadata, while
                # call/root/parent ids remain queryable without parsing model-facing text.
                event.metadata["execution"] = dict(execution_meta)

        if event is not None:
            await trace_manager.emit(event)
            if event.event_type is TraceEventType.AGENT_CALL and event.usage and trace_manager.log_root:
                # The request snapshot is committed before dispatch, while provider
                # usage arrives only when the step closes. Refresh the same HTML page
                # with the measured cache/cost data; the canonical snapshot is unchanged.
                requests = [
                    candidate
                    for candidate in trace_manager.events(str(event.session_id or ""))
                    if candidate.event_type is TraceEventType.MODEL_REQUEST
                    and candidate.agent_name == event.agent_name
                ]
                current = next(
                    (
                        candidate for candidate in reversed(requests)
                        if candidate.step_number == event.step_number
                    ),
                    None,
                )
                if current is not None:
                    previous = next(
                        (
                            candidate for candidate in reversed(requests)
                            if int(candidate.step_number or 0) < int(current.step_number or 0)
                        ),
                        None,
                    )
                    try:
                        from agentevolver.visual.request_viewer import (
                            request_log_root,
                            schedule_request_html,
                        )

                        schedule_request_html(
                            current,
                            request_log_root(trace_manager.log_root),
                            usage=event.usage,
                            previous_event=previous,
                        )
                    except Exception as render_error:  # noqa: BLE001 - observational only
                        from agentevolver.logger import logger

                        logger.debug(f"| model request HTML usage refresh was not scheduled: {render_error}")
            # Memory consumes this exact object after Trace has assigned seq_no. It used
            # to construct a second look-alike event, which made compaction unable to cite
            # the durable log it was summarising.
            from agentevolver.memory import memory_manager

            await memory_manager.consume_trace_event(
                event,
                memory_name=inp.get("memory_name"),
                enabled=bool(inp.get("use_memory", False)),
            )

        return HookResult.allow()

    def _task_id(self, ctx: HookContext) -> str:
        """Return the payload's ``task_id``, falling back to the session id."""
        if not ctx.input:
            return ctx.id
        return ctx.input.get("task_id") or ctx.id

    def _task_content(self, ctx: HookContext) -> str:
        """Return the payload's ``task`` description, or an empty string if absent."""
        if not ctx.input:
            return ""
        return ctx.input.get("task") or ""

    def _pop_timer(self, key: str) -> Optional[float]:
        """Pop the timer at ``key`` and return the elapsed time in milliseconds (``None`` if unset)."""
        start = self._timers.pop(key, None)
        return None if start is None else (time.monotonic() - start) * 1000

    def _add_usage(self, session_id: str, usage: Optional[Dict]) -> None:
        """Fold one step's cost into the session total.

        A step whose usage the provider did not report is skipped rather than counted as
        zero: a missing figure and a free call are different facts, and adding the former
        as the latter makes the total read as authoritative when it is short.
        """
        if not usage:
            return
        running = self._usage.setdefault(session_id, {"steps": 0})
        running["steps"] += 1
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                running[key] = running.get(key, 0) + value
