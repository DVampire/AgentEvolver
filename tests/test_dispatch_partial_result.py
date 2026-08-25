"""A dispatched sub-agent that runs out of budget still did the work — a worker that hits
its step ceiling has usually written real code — but it returns success=False because it
never called done_tool. The orchestrator must receive that partial result (plus the
envelope that marks it PARTIAL) and decide what to do next, not a bare exception that
discards the work behind the word "failed". This matters more with a tight worker budget,
which makes ceiling hits routine.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import types

import pytest

from agentevolver.response import Response, ResponseType


def _call_invoke(resp_from_delegate):
    """Drive `_invoke_capability` on an agent route with `runtime_manager.delegate` stubbed
    to return `resp_from_delegate`. Returns (output, error) or raises what it raised."""
    from agentevolver.agent.actor.general_agent import GeneralAgent
    import agentevolver.agent.server as server_mod
    import agentevolver.runtime as runtime_mod

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/claude-1014")

    async def fake_get(name):
        return object()  # a stand-in child; delegate is what we stub

    async def fake_delegate(child, task, **brief):
        return resp_from_delegate

    server_mod.agent_manager.get = fake_get
    runtime_mod.runtime_manager.delegate = fake_delegate

    route = ("agent", "code_agent")
    call = types.SimpleNamespace(input={"task": "implement the query command"}, id="c1", name="code_agent")

    result = asyncio.run(agent._invoke_capability(route, call, ctx=None))
    # _invoke_capability returns a 6-tuple: (output, is_done, result, reasoning, error, meta)
    return result[0], result[4]


def test_a_ceiling_hit_hands_back_the_partial_result_not_an_exception():
    resp = Response(
        type=ResponseType.AGENT, success=False,
        message="Implemented src/cmd/query.rs; the aging edge case is unfinished.",
        data={"done": False, "stopped_by_constraint": False, "step": 50, "max_step": 50},
    )
    output, error = _call_invoke(resp)
    assert "Implemented src/cmd/query.rs" in output, "the worker's partial result was lost"
    assert "PARTIAL" in output and "step ceiling" in output, "the PARTIAL envelope must be attached"
    assert error is None, "a cut-off worker is an observation to build on, not a hard error"


def test_a_clean_finish_still_carries_the_finished_envelope():
    resp = Response(
        type=ResponseType.AGENT, success=True,
        message="Done: query command implemented and verified.",
        data={"done": True, "stopped_by_constraint": False, "step": 30, "max_step": 50},
    )
    output, error = _call_invoke(resp)
    assert "query command implemented" in output
    assert "finished" in output and "30/50 steps" in output
    assert error is None


def test_an_empty_failed_dispatch_still_returns_rather_than_raises():
    # Even with nothing produced, the orchestrator gets a labelled observation it can act on
    # (retry, change strategy), not an exception that aborts its turn.
    resp = Response(
        type=ResponseType.AGENT, success=False, message="",
        data={"done": False, "stopped_by_constraint": False, "step": 3, "max_step": 50},
    )
    output, error = _call_invoke(resp)
    assert "PARTIAL" in output
    assert error is None
