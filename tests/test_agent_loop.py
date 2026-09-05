"""The think-and-act loop: one turn in, one turn recorded, and the gates around it.

The loop's job is to keep the conversation sendable. Every property here is a way that
can fail: a turn whose tool results do not answer its calls, a batch that reorders
effects, a refusal that vanishes instead of reaching the model, a run that never stops.
"""

from typing import Any, Dict, List, Sequence

import pytest

from agentevolver.agent.loop import ActionCall, ActionResult, Agent, Decision, ToolRouter
from agentevolver.agent.loop.guards import BudgetExhausted, Constraints, LandingWindow, NoProgress
from agentevolver.message.types import AssistantMessage, SystemMessage, ToolMessage

_ids = iter(range(1, 10**6))


def calls(*pairs) -> Decision:
    """A decision that wants to run things. Ids are unique, as a provider's are."""
    return Decision(calls=[
        ActionCall(id=f"c{next(_ids)}", name=name, args=args) for name, args in pairs
    ])


class StubRouter(ToolRouter):
    """A router over plain callables: no registry, no managers, no network."""

    def __init__(self, tools: Dict[str, Any], read_only: Sequence[str] = ()):
        self.tools = tools
        self.read_only_names = set(read_only)
        self.invoked: List[str] = []
        self.peak = 0
        self._active = 0

    async def schemas(self, agent, ctx):
        return [{"name": name} for name in self.tools], {
            name: ("tool", name) for name in self.tools
        }

    def read_only(self, call, routing):
        return True if call.name in self.read_only_names else None

    async def invoke(self, call, *, agent, ctx, routing, execution=None):
        import asyncio

        self.invoked.append(call.name)
        self._active += 1
        self.peak = max(self.peak, self._active)
        try:
            await asyncio.sleep(0.01)
            outcome = self.tools[call.name](call.args)
            if isinstance(outcome, Exception):
                return ActionResult(call=call, error=str(outcome))
            return ActionResult(call=call, output=str(outcome))
        finally:
            self._active -= 1


class Scripted(Agent):
    """An agent whose `think` reads a script instead of calling a model."""

    def __init__(self, script: Sequence[Decision], **kwargs):
        super().__init__(**kwargs)
        self.script = list(script)
        self.seen_live: List[List[str]] = []

    async def think(self, step, live=()):
        self.seen_live.append(list(live))
        _, routing = await self.router.schemas(self, self.ctx)
        self._routing = routing
        # Built every step, because building is what validates the conversation.
        self.assembler.build(self.conversation, live=live)
        if step < len(self.script):
            return self.script[step]
        return Decision(text="off the end of the script")

    async def system_messages(self, ctx):
        return [SystemMessage(content="You are a test agent.")]

    def project_context(self, ctx):
        return ""


TOOLS = {
    "read": lambda args: f"contents of {args.get('path')}",
    "grep": lambda args: "3 matches",
    "write": lambda args: f"wrote {args.get('path')}",
    "boom": lambda args: RuntimeError("disk full"),
}


@pytest.mark.asyncio
async def test_native_async_read_overlaps_generation_and_is_not_repeated(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from agentevolver.model.types import ToolCallComplete, TextDelta, StreamDone

    started, finish = asyncio.Event(), asyncio.Event()
    class Router(StubRouter):
        async def schemas(self, agent, ctx):
            return [SimpleNamespace(name="read", metadata={"programmatic": True})], {"read": ("tool", "read")}

        async def invoke(self, call, **kwargs):
            self.invoked.append(call.name)
            started.set()
            await finish.wait()
            return ActionResult(call=call, output="42")

    router = Router(TOOLS, read_only=["read"])
    agent = Agent(router=router, async_tool_calling=True)
    async def stream(**kwargs):
        assert kwargs["input"]["runtime_features"]["async_tool_calling"]
        yield ToolCallComplete(0, "call1", "read", {"path": "x"}, asynchronous=True)
        await asyncio.wait_for(started.wait(), 1)
        yield TextDelta("Independent reasoning while the read is running")
        finish.set()
        yield StreamDone("tool_use")

    monkeypatch.setattr("agentevolver.model.model_manager", SimpleNamespace(stream=stream))
    decision = await agent.think(0)
    assert not decision.error
    assert router.invoked == ["read"]
    results = await agent.act(decision)
    assert results[0].output == "42" and router.invoked == ["read"]
    agent.conversation.add_turn(decision.as_assistant(), [r.as_message() for r in results])
    agent.assembler.build(agent.conversation)  # unchanged complete-turn validation


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["disconnect", "cancel", "max_tokens"])
async def test_async_read_is_reaped_when_generation_stops(ending):
    import asyncio
    from agentevolver.model.types import ToolCallComplete, StreamDone
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Router(StubRouter):
        async def invoke(self, call, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    agent = Agent(router=Router(TOOLS, read_only=["read"]), async_tool_calling=True)
    async def stream():
        yield ToolCallComplete(0, "c1", "read", {}, asynchronous=True)
        await asyncio.wait_for(started.wait(), 1)
        if ending == "disconnect":
            raise ConnectionError("stream dropped")
        if ending == "cancel":
            raise asyncio.CancelledError()
        yield StreamDone("max_tokens")

    async def consume():
        return await agent.executor.consume(stream(), agent=agent, ctx=None,
                                            routing={}, eligible={"read"})
    if ending == "max_tokens":
        _, prepared = await consume()
        assert prepared == {}
    else:
        with pytest.raises(ConnectionError if ending == "disconnect" else asyncio.CancelledError):
            await consume()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_async_never_reorders_a_read_after_a_write():
    from agentevolver.model.types import ToolCallComplete, StreamDone
    router = StubRouter(TOOLS, read_only=["read"])
    agent = Agent(router=router, async_tool_calling=True)
    async def stream():
        yield ToolCallComplete(0, "w", "write", {}, asynchronous=True)
        yield ToolCallComplete(1, "r", "read", {}, asynchronous=True)
        yield StreamDone("tool_use")
    accumulated, prepared = await agent.executor.consume(
        stream(), agent=agent, ctx=None, routing={}, eligible={"read", "write"},
    )
    assert not prepared and not router.invoked
    await agent.act(agent._decide(accumulated))
    assert router.invoked == ["write", "read"]


@pytest.mark.asyncio
async def test_action_limit_returns_skips_without_breaking_tool_protocol():
    router = StubRouter(TOOLS, read_only=["read"])
    agent = Agent(router=router, max_actions=1)
    decision = calls(("read", {}), ("write", {}))
    results = await agent.act(decision)
    assert len(results) == 2 and results[1].error
    assert router.invoked == ["read"]
    agent.conversation.add_turn(decision.as_assistant(), [r.as_message() for r in results])
    agent.assembler.build(agent.conversation)


def test_native_caller_identity_survives_agent_action_roundtrip():
    caller = {"type": "programmatic", "id": "program1"}
    call = ActionCall("c1", "read", {}, caller)
    assert call.as_tool_call().caller == caller
    assert ActionResult(call=call, output="42").as_message().caller == caller


async def _completed(value):
    """A coroutine that just returns `value`, for patching an async seam."""
    return value


@pytest.mark.asyncio
async def test_native_compaction_receives_the_previous_checkpoint(monkeypatch):
    from agentevolver.message.types import CompactionMessage
    from agentevolver.model import model_manager

    agent = Scripted([])
    checkpoint = CompactionMessage(content="previous discoveries")
    agent.conversation.checkpoint = checkpoint
    new_turn = AssistantMessage(content="new discovery")
    captured = []

    async def compact(self, name, messages, **kwargs):
        captured.extend(messages)
        return None

    monkeypatch.setattr(type(model_manager), "compact_history", compact)
    await agent.native_checkpoint([new_turn])
    assert captured == [checkpoint, new_turn]


def make(script, *, read_only=("read", "grep"), **kwargs) -> Scripted:
    router = StubRouter(TOOLS, read_only=read_only)
    settings = {"name": "probe", "max_step": 10, **kwargs}
    return Scripted(script, router=router, **settings)


# ---------------------------------------------------------------------------
# A turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_is_recorded_whole_and_the_answer_ends_the_run():
    agent = make([
        calls(("read", {"path": "a.py"})),
        calls(("write", {"path": "b.py"})),
        Decision(text="done: wrote b.py"),
    ])
    response = await agent("fix the bug")

    assert response.success
    assert response.message == "done: wrote b.py"
    # Every emitted call has its result, which is the only shape a provider accepts.
    assert agent.conversation.complete
    assert agent.conversation.turns == 3


@pytest.mark.asyncio
async def test_a_turn_with_no_tool_call_is_the_answer_and_needs_no_done_tool():
    agent = make([Decision(text="the timeout is 30s")])
    response = await agent("where is the timeout set?")
    assert response.success
    assert response.message == "the timeout is 30s"
    assert agent.step == 0


@pytest.mark.asyncio
async def test_a_finish_tool_also_ends_the_run():
    class Finishing(StubRouter):
        async def invoke(self, call, *, agent, ctx, routing, execution=None):
            return ActionResult(call=call, output="all checks pass", final=True)

    agent = Scripted([calls(("done_tool", {}))], router=Finishing({"done_tool": lambda a: ""}),
                     name="probe", max_step=5)
    response = await agent("finish")
    assert response.success
    assert response.message == "all checks pass"


@pytest.mark.asyncio
async def test_the_step_budget_stops_the_run_and_says_so():
    agent = make([calls(("read", {"path": str(i)})) for i in range(20)], max_step=3)
    response = await agent("keep reading")
    assert not response.success
    assert "step budget" in response.message


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_read_only_batch_runs_together_and_a_mixed_one_does_not():
    parallel = make([calls(("read", {"path": "x"}), ("grep", {"q": "y"})),
                     Decision(text="ok")])
    await parallel("look")

    serial = make([calls(("read", {"path": "x"}), ("write", {"path": "y"})),
                   Decision(text="ok")], read_only=("read",))
    await serial("mix")

    assert parallel.router.peak == 2
    assert serial.router.peak == 1


@pytest.mark.asyncio
async def test_a_failed_batch_stops_but_every_call_still_gets_a_result():
    agent = make([calls(("boom", {}), ("write", {"path": "z"})), Decision(text="ok")],
                 read_only=())
    await agent("go")

    assistant = next(m for m in agent.conversation.items if isinstance(m, AssistantMessage))
    results = [m for m in agent.conversation.items if isinstance(m, ToolMessage)]
    assert len(results) == len(assistant.tool_calls)
    assert agent.conversation.complete
    # The second call never ran, and says why rather than being absent.
    assert agent.router.invoked == ["boom"]
    assert "disk full" in results[0].content
    assert "Not executed" in results[1].content


@pytest.mark.asyncio
async def test_an_action_failure_reaches_the_next_step_as_context():
    agent = make([calls(("boom", {})), Decision(text="ok")], read_only=())
    await agent("go")
    assert any("boom" in block for block in agent.seen_live[1])


@pytest.mark.asyncio
async def test_a_turn_over_the_action_ceiling_is_trimmed():
    agent = make([calls(*[("read", {"path": str(i)}) for i in range(8)]),
                  Decision(text="ok")], max_actions=3)
    await agent("fan out")
    assert len(agent.router.invoked) == 3


# ---------------------------------------------------------------------------
# Gates and guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_read_only_agent_is_refused_a_mutating_tool_as_a_result():
    """The refusal is data. An exception would leave the assistant turn unanswered."""
    from agentevolver.agent.loop.router import CapabilityRouter

    class Denying(StubRouter):
        def denial(self, call, routing, agent):
            return CapabilityRouter.denial(self, call, routing, agent)

    router = Denying(TOOLS, read_only=())
    agent = Scripted([calls(("write_file_tool", {})), Decision(text="ok")],
                     router=router, name="probe", permission_mode="read_only", max_step=5)
    router.tools["write_file_tool"] = lambda args: "should not run"
    await agent("try to write")

    results = [m for m in agent.conversation.items if isinstance(m, ToolMessage)]
    assert results and results[0].is_error
    assert "read_only" in results[0].content
    assert agent.conversation.complete


@pytest.mark.asyncio
async def test_the_landing_window_fires_only_near_the_end():
    agent = make([calls(("read", {"path": str(i)})) for i in range(6)], max_step=6)
    agent.middleware = [LandingWindow(reserve=2)]
    await agent("keep looking")
    fired = ["<budget>" in "\n".join(blocks) for blocks in agent.seen_live]
    assert fired[:4] == [False] * 4
    assert fired[4:] == [True] * 2


@pytest.mark.asyncio
async def test_the_no_progress_guard_notices_a_run_that_only_inspects():
    agent = make([calls(("read", {"path": str(i)})) for i in range(6)], max_step=6)
    agent.middleware = [NoProgress(after=3)]
    await agent("keep looking")
    assert any("<no-progress>" in "\n".join(blocks) for blocks in agent.seen_live)


@pytest.mark.asyncio
async def test_a_spent_budget_stops_the_run_at_a_step_boundary():
    class Spent:
        name = "token_budget"

        def _cleanup(self, key):
            pass

    class Blocking(Constraints):
        async def __call__(self, agent, step):
            raise BudgetExhausted("token budget is spent")

    agent = make([calls(("read", {"path": "x"})) for _ in range(5)])
    agent.middleware = [Blocking([Spent()])]
    response = await agent("go")
    assert not response.success
    assert "token budget is spent" in response.message
    # Stopped before the model was asked, so nothing half-recorded.
    assert agent.conversation.turns == 0


# ---------------------------------------------------------------------------
# Model faults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [
    {"input_tokens": 12, "output_tokens": 1},
    {"context_input_tokens": 12, "input_tokens": 2, "cache_read_tokens": 10,
     "output_tokens": 1, "reasoning_tokens": 1},
])
async def test_declared_budget_is_mandatory_and_counts_input(usage):
    decision = calls(("read", {"path": "x"}))
    decision.usage = usage
    agent = make([decision, Decision(text="should not run")])
    agent.max_token = 10
    agent.middleware = []
    result = await agent("go")
    assert not result.success and "Token limit" in result.message
    assert agent._used_tokens == 13
    assert not agent.router.invoked
    assert agent.conversation.complete


@pytest.mark.asyncio
async def test_budget_is_reset_per_assignment_and_checks_final_usage():
    agent = make([Decision(text="done", usage={"input_tokens": 5, "output_tokens": 1})])
    agent.max_token = 7
    assert (await agent("one")).success
    assert (await agent("two")).success
    agent.max_token = 6
    assert not (await agent("three")).success


@pytest.mark.asyncio
async def test_wall_budget_cancels_inflight_think(monkeypatch):
    import asyncio
    ended = asyncio.Event()
    agent = make([Decision(text="unused")])
    agent.timeout = .05

    async def slow(step, live=()):
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    monkeypatch.setattr(agent, "think", slow)
    result = await agent("go")
    assert not result.success and "Wall-clock budget" in result.message
    assert ended.is_set()


@pytest.mark.asyncio
async def test_unrelated_timeout_is_not_an_assignment_timeout(monkeypatch):
    agent = make([Decision(text="unused")])
    agent.timeout = 10

    async def broken(step, live=()):
        raise TimeoutError("tool connection timed out")

    monkeypatch.setattr(agent, "think", broken)
    with pytest.raises(TimeoutError, match="tool connection"):
        await agent("go")


def test_fresh_agents_do_not_share_stateful_guards():
    from agentevolver.agent.actor.meta_agent import MetaAgent
    template = MetaAgent()
    one, two = template.fresh(), template.fresh()
    for original, first, second in zip(template.middleware, one.middleware, two.middleware):
        assert original is not first and first is not second


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "agent", "environment", "workflow"])
async def test_denied_capabilities_never_execute(kind):
    class Denied(StubRouter):
        def denial(self, call, routing, agent):
            return "not permitted"

    router = Denied(TOOLS)
    agent = Agent(router=router)
    result = await agent.executor.run(
        [ActionCall(id="c", name="read")], agent=agent, ctx=None,
        routing={"read": (kind, "read")},
    )
    assert result[0].error == "not permitted"
    assert not router.invoked


@pytest.mark.asyncio
async def test_empty_tool_grant_prevents_implicit_file_context():
    from agentevolver.agent.types import AgentContext
    agent = Agent(use_memory=True)
    ctx = AgentContext(extra={"tool_allowlist": []})
    assert agent.project_context(ctx) == ""
    assert await agent.memory_context(ctx) == ""


@pytest.mark.asyncio
async def test_empty_environment_grant_skips_state_reads(monkeypatch):
    from agentevolver.agent.types import AgentContext
    agent = Agent(env_names=["browser_environment"])
    assert await agent.environment_state(AgentContext(extra={"environment_allowlist": []})) == ""


@pytest.mark.asyncio
async def test_checkpoint_usage_counts_against_same_budget(monkeypatch):
    from types import SimpleNamespace
    from agentevolver.model import model_manager
    from agentevolver.hook.server import hook_manager

    agent = Agent(max_token=50)

    async def compact(*args, **kwargs):
        return {"format": "test", "usage": {"input_tokens": 20, "output_tokens": 5}}

    async def summary(self, **kwargs):
        return SimpleNamespace(output="checkpoint", usage={"input_tokens": 20, "output_tokens": 5})

    monkeypatch.setattr(type(model_manager), "compact_history", compact)
    monkeypatch.setattr(type(hook_manager), "__call__", summary)
    await agent.native_checkpoint([SystemMessage(content="history")])
    assert agent._used_tokens == 25
    with pytest.raises(BudgetExhausted):
        await agent.text_checkpoint([SystemMessage(content="history")])
    assert agent._used_tokens == 50



@pytest.mark.asyncio
async def test_repeated_model_errors_stop_the_run_and_report_the_cause():
    agent = make([Decision(error="Model 'x' not found") for _ in range(5)])
    response = await agent("go")
    assert not response.success
    assert "not found" in response.message
    assert agent.step < agent.max_step  # gave up early rather than burning the budget


@pytest.mark.asyncio
async def test_a_truncated_turn_is_discarded_rather_than_dispatched():
    agent = make([
        Decision(text="half a call", stop_reason="max_tokens"),
        Decision(text="ok"),
    ])
    response = await agent("write a big file")
    assert response.success
    assert agent.router.invoked == []
    assert any("output limit" in block for block in agent.seen_live[1])


@pytest.mark.asyncio
async def test_an_overflow_folds_history_and_retries_instead_of_counting_as_a_failure():
    folded: List[int] = []

    class Overflowing(Scripted):
        async def make_room(self):
            folded.append(self.step)
            return True

    agent = Overflowing(
        [Decision(error="does not fit", overflowed=True), Decision(text="ok")],
        router=StubRouter(TOOLS), name="probe", max_step=5,
    )
    response = await agent("go")
    assert response.success
    assert folded == [0]
    assert agent._model_failures == 0


# ---------------------------------------------------------------------------
# What a fold tells observers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_folding_history_is_announced_before_and_after(monkeypatch):
    """PRE_COMPACT / POST_COMPACT existed but only the memory tier raised them.

    The fold that changes what the model sees raised nothing, so a trace showed the
    token count halve between two steps with no record of why — which reads like a
    metering fault rather than the compaction it was.
    """
    from agentevolver.hook.events import HookEvent

    agent = make([calls(("read", {"path": "a"}))])
    seen: List[Any] = []

    async def emit(event, payload=None, *, ctx=None):
        seen.append((event, payload or {}))

    monkeypatch.setattr(agent._events, "emit", emit)
    monkeypatch.setattr(
        agent, "text_checkpoint", lambda source: _completed("a summary of five turns")
    )
    agent.compact_verify = False  # This test pins compaction event ordering.
    for index in range(6):
        agent.conversation.append(AssistantMessage(content=f"turn {index}", tool_calls=[]))
    agent.assembler.compact_after_turns = 1

    assert await agent.make_room(trigger="turns") is True

    kinds = [event for event, _ in seen]
    assert kinds == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT]
    before, after = seen[0][1], seen[1][1]
    assert before["trigger"] == "turns" and before["fold"] == 1
    assert after["folded"] is True and after["detail"] == "text"
    # The pair is what makes the drop explainable: a number, then the same number again.
    assert after["tokens_before"] == before["tokens"]
    assert after["tokens_after"] < after["tokens_before"]


@pytest.mark.asyncio
async def test_a_fold_that_moves_nothing_still_says_so(monkeypatch):
    """A refusal has to be observable too, or a run that cannot shrink looks idle."""
    from agentevolver.hook.events import HookEvent

    agent = make([])
    seen: List[Any] = []

    async def emit(event, payload=None, *, ctx=None):
        seen.append((event, payload or {}))

    monkeypatch.setattr(agent._events, "emit", emit)
    agent._folds = agent.assembler.max_folds

    assert await agent.make_room() is False
    assert [event for event, _ in seen] == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT]
    assert seen[1][1]["folded"] is False
    assert seen[1][1]["detail"] == "fold budget spent"


# ---------------------------------------------------------------------------
# A gate that cannot open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_lands_when_one_blocker_refuses_it_over_and_over():
    """A contract gate whose input cannot change is a protocol fault, not a retry.

    Measured on a live website run: the deploy gate held on a subscriber verdict that
    could never be updated, `done_tool` held on the release count that deploy could not
    raise, and a middleware asked for a preview each step. The agent spent 58 of its 133
    steps — 43% — alternating between them and ended cancelled by a human. Nothing in
    the loop could recognise that the same sentence had come back unchanged.
    """
    class Blocked(Scripted):
        async def completion_blocker(self, ctx):
            return "release 1 subscriber turns failed: sub-b"

    agent = make([Decision(text="done") for _ in range(20)], max_step=20)
    agent.__class__ = type("BlockedProbe", (Blocked, type(agent)), {})

    response = await agent("build the thing")
    assert response.success is False
    assert "Protocol blocker" in response.message
    assert "sub-b" in response.message
    # Landed on the threshold rather than burning the rest of the budget.
    assert agent.step < 19, f"landed at step {agent.step}, budget was 20"


@pytest.mark.asyncio
async def test_a_blocker_that_keeps_changing_is_progress_and_is_not_cut_short():
    """A moving gate means the run is getting somewhere; only a frozen one is stuck."""
    class Moving(Scripted):
        async def completion_blocker(self, ctx):
            remaining = 8 - self.step
            return f"{remaining} releases still required" if remaining > 0 else None

    agent = make([Decision(text="done") for _ in range(20)], max_step=20)
    agent.__class__ = type("MovingProbe", (Moving, type(agent)), {})

    response = await agent("build the thing")
    assert response.success is True, response.message
