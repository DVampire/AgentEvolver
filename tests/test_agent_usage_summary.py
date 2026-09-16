"""Agent-end usage includes auxiliary model work without adding step totals twice."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.types import Agent
from agentevolver.hook.default.trace import TraceHook
from agentevolver.hook.types import HookContext, HookEvent
from agentevolver.response.types import Response, ResponseType


@pytest.mark.asyncio
async def test_stop_summary_includes_generation_and_compaction(monkeypatch):
    from agentevolver.memory import memory_manager
    from agentevolver.trace import trace_manager

    generation = {"input_tokens": 10, "output_tokens": 5, "cost": .3}
    compact = {"input_tokens": 20, "output_tokens": 2, "cost": .1}
    emitted = []

    async def emit(event):
        emitted.append(event)

    monkeypatch.setattr(trace_manager, "emit", emit)
    monkeypatch.setattr(type(memory_manager), "consume_trace_event", AsyncMock())
    hook = TraceHook()
    await hook.handle(HookContext(id="run", name="trace", input={
        "event": HookEvent.POST_STEP, "agent_name": "builder", "step_number": 0, "step_usage": generation}))
    agent = Agent()
    agent.ctx = SimpleNamespace(id="run", extra={})
    agent.count_usage(generation)
    agent.count_usage(compact)

    async def observed(event, payload, **kwargs):
        await hook.handle(HookContext(id="run", name="trace", input={"event": event, **payload}))

    monkeypatch.setattr(Agent, "_events", property(lambda self: SimpleNamespace(emit=observed)))
    await agent._emit_stop(Response(type=ResponseType.AGENT, success=True, message="done"))
    usage = emitted[-1].usage
    assert usage["steps"] == 1
    assert usage["cost"] == pytest.approx(.4)
    assert usage["input_tokens"] == 30 and usage["output_tokens"] == 7
    assert agent._used_tokens == 37
