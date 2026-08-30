"""A context that will not fit is answered by making room, once, and then honestly.

`prepare_messages` may only reduce tool results — rewriting user, system or assistant
messages would change instructions or sever tool-call structure — so a long run reaches a
point where the boundary has nothing left it is allowed to shrink. Before this, the
oversized request went out anyway, the provider rejected it, the retry policy read that
rejection as transient and re-sent the identical request until its budget ran out, and the
run died reporting a provider failure.

Folding is the only recovery that exists. The prompt is rebuilt from memory and trace on
every step, so a summary recorded now is shadowed out of the derived history before the
next render — nothing else the run can do makes the same request smaller.

It is also lossy, which is why it is bounded. A run that needs to fold on every step is
not recovering; it is shrinking toward a prompt that no longer describes its own task, and
the honest end is to say the context does not fit rather than to keep cutting.
"""

from __future__ import annotations

import pytest

from agentevolver.agent.types import _COMPACTIONS_PER_RUN


class _Run:
    """Only what `_make_room` reads: the compaction counter and a session id."""

    def __init__(self):
        self.rooms_made = 0
        self.ctx = type("Ctx", (), {"id": "run-session"})()


class _Agent:
    """`_make_room` unbound from Agent, so this exercises the method and not a rebuild
    of the agent's whole construction path."""

    from agentevolver.agent.types import Agent

    _make_room = Agent._make_room

    def __init__(self, *, use_memory: bool = True, memory_name: str = "tiered_memory"):
        self.name = "actor"
        self.use_memory = use_memory
        self.memory_name = memory_name
        self.retain_recent_steps = 10


@pytest.fixture
def folds(monkeypatch):
    """Record every compaction request, and control what it answers."""
    calls: list = []
    answers = {"value": True}

    async def compact(memory_name, session_id, **kwargs):
        calls.append((memory_name, session_id, kwargs))
        return answers["value"]

    from agentevolver.memory import memory_manager
    monkeypatch.setattr(memory_manager, "compact", compact)
    return calls, answers


@pytest.mark.asyncio
async def test_an_oversized_context_is_answered_by_folding(folds):
    calls, _ = folds
    run = _Run()

    assert await _Agent()._make_room(run) is True
    assert calls == [("tiered_memory", "run-session", {"keep_steps": 10})]
    assert run.rooms_made == 1


@pytest.mark.asyncio
async def test_a_memory_with_nothing_left_to_fold_ends_the_attempt(folds):
    """The run then reports that the context does not fit, which is what is true.

    Reading "folded" from a fold that removed nothing would rebuild the same oversized
    request and ask again, for as long as the budget lasted.
    """
    calls, answers = folds
    answers["value"] = False

    assert await _Agent()._make_room(_Run()) is False
    assert len(calls) == 1, "asked again after being told there was nothing to fold"


@pytest.mark.asyncio
async def test_folding_is_bounded_within_one_run(folds):
    """Folding is lossy. A run that needs it every step is not recovering."""
    calls, _ = folds
    agent, run = _Agent(), _Run()

    for _ in range(_COMPACTIONS_PER_RUN):
        assert await agent._make_room(run) is True

    assert await agent._make_room(run) is False
    assert len(calls) == _COMPACTIONS_PER_RUN, "folded past the bound"


@pytest.mark.asyncio
async def test_an_agent_without_memory_has_nothing_to_fold(folds):
    calls, _ = folds

    assert await _Agent(use_memory=False)._make_room(_Run()) is False
    assert calls == []


@pytest.mark.asyncio
async def test_a_failing_memory_does_not_replace_the_overflow_report(monkeypatch):
    """The run is already reporting something true. An error from the attempted repair
    must not become the thing the operator reads instead."""
    async def explode(memory_name, session_id):
        raise RuntimeError("memory backend is down")

    from agentevolver.memory import memory_manager
    monkeypatch.setattr(memory_manager, "compact", explode)

    assert await _Agent()._make_room(_Run()) is False


def test_the_loop_answers_an_overflow_before_counting_it_as_a_model_failure():
    """Read from the source: the two branches are ordered, and the order is the point.

    Counted first, an unfittable context would spend the give-up budget on three
    identical rebuilds and report a model error, which is the behaviour this replaced.
    """
    import inspect
    from agentevolver.agent.types import Agent

    source = inspect.getsource(Agent)
    recovery = source.index('decision.get("overflowed")')
    counted = source.index("run.think_failures += 1")
    assert recovery < counted, "an overflow is counted as a model failure before it is answered"


# --------------------------------------------------------------------------- #
# Folding before the ceiling, when a deployment asks for it
# --------------------------------------------------------------------------- #
# Off by default, and the default is a judgement rather than caution: the recovery path
# costs no tokens when it fires — an oversized request is refused before it is sent — so
# folding at 85% spends information to save a loop iteration on requests that would
# mostly have succeeded. It is worth having because the deterministic prune in that band
# is lossy too, and which loses less is a question about a workload.


class _Ahead(_Agent):
    """`_fold_ahead` and its reading, unbound, over a controllable log."""

    from agentevolver.agent.types import Agent

    _fold_ahead = Agent._fold_ahead
    _last_pressure_ratio = Agent._last_pressure_ratio
    _context_history_metrics = Agent._context_history_metrics

    def __init__(self, *, fold_at_pressure: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self.fold_at_pressure = fold_at_pressure
        self.compact_after_steps = 0
        self.compact_at_tokens = 0
        self.compact_uncached_growth = 0


@pytest.fixture
def at_pressure(monkeypatch):
    """Serve one recorded request pressure to `_last_pressure_ratio`."""
    from agentevolver.trace import trace_manager
    from agentevolver.trace.types import TraceEvent, TraceEventType

    ratio = {"value": 0.0}

    def events(session_id, *args, **kwargs):
        return [TraceEvent(event_type=TraceEventType.MODEL_REQUEST, session_id="s", seq_no=0,
                           input={"pressure": {"pressure_ratio_after": ratio["value"]}})]

    monkeypatch.setattr(trace_manager, "events", events)
    return ratio


def test_folding_ahead_is_off_unless_a_deployment_sets_a_threshold():
    """Read off the real signature.

    A stub that passes its own threshold cannot see the default, so asserting behaviour
    through one says nothing about what an ordinary agent does — which is the whole
    claim. Same reason `derive_context` pins its default this way.
    """
    import inspect
    from agentevolver.agent.types import Agent

    assert inspect.signature(Agent.__init__).parameters["fold_at_pressure"].default == 0.85


@pytest.mark.asyncio
async def test_a_zero_threshold_is_inert_under_pressure_that_would_trigger_it(folds, at_pressure):
    calls, _ = folds
    at_pressure["value"] = 0.99

    await _Ahead(fold_at_pressure=0.0)._fold_ahead(_Run())

    assert calls == [], "folded ahead without being asked to"


@pytest.mark.asyncio
async def test_a_deployment_that_asks_for_it_folds_ahead(folds, at_pressure):
    calls, _ = folds
    at_pressure["value"] = 0.90

    await _Ahead(fold_at_pressure=0.85)._fold_ahead(_Run())

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_request_below_the_threshold_is_left_alone(folds, at_pressure):
    calls, _ = folds
    at_pressure["value"] = 0.50

    await _Ahead(fold_at_pressure=0.85)._fold_ahead(_Run())

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,metric",
    [
        ("compact_after_steps", 30, {"logical_steps": 30}),
        ("compact_at_tokens", 120_000, {"estimated_tokens": 120_000}),
        ("compact_uncached_growth", 50_000, {"uncached_growth": 50_000}),
    ],
)
async def test_early_compaction_triggers_before_window_pressure(
    folds, field, value, metric
):
    calls, _ = folds
    agent = _Ahead(fold_at_pressure=0.85)
    setattr(agent, field, value)
    measured = {
        "logical_steps": 0,
        "estimated_tokens": 0,
        "pressure_ratio": 0.10,
        "uncached_growth": 0,
        **metric,
    }
    agent._context_history_metrics = lambda run: measured

    await agent._fold_ahead(_Run())

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_folding_ahead_shares_the_runs_budget(folds, at_pressure):
    """Otherwise a pressured run folds every step for the rest of its budget, and the
    bound that makes folding safe would apply only to the recovery path."""
    calls, _ = folds
    at_pressure["value"] = 0.99
    agent, run = _Ahead(fold_at_pressure=0.85), _Run()

    for _ in range(_COMPACTIONS_PER_RUN + 2):
        await agent._fold_ahead(run)

    assert len(calls) == _COMPACTIONS_PER_RUN


@pytest.mark.asyncio
async def test_a_log_that_cannot_be_read_does_not_fold_on_a_guess(folds, monkeypatch):
    """Measuring nothing is not the same as measuring high."""
    calls, _ = folds
    from agentevolver.trace import trace_manager

    def explode(session_id, *args, **kwargs):
        raise RuntimeError("trace backend is down")

    monkeypatch.setattr(trace_manager, "events", explode)
    await _Ahead(fold_at_pressure=0.85)._fold_ahead(_Run())

    assert calls == []


def test_the_fold_happens_before_the_prompt_is_rebuilt():
    """A fold after `_get_messages` would take effect one step late, which reads as the
    threshold being off by one rather than as an ordering mistake."""
    import inspect
    from agentevolver.agent.types import Agent

    # Scoped to the method that does both. `_get_messages` is called from more than one
    # place, and the earliest call in the class is not the one this orders against.
    body = next(
        inspect.getsource(member) for _, member in inspect.getmembers(Agent, inspect.isfunction)
        if "await self._fold_ahead(run)" in inspect.getsource(member)
    )
    assert body.index("await self._fold_ahead(run)") < body.index("await self._get_messages(")
