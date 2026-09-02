"""The agent loop can be driven end to end with no API key.

The loop is the largest untested surface in this repository — 6,700 lines at 43% — and the
reason was never that nobody tried. Every path through it needs a model, so every test of
it needed a key, a network, and a provider that answers the same way twice. CI has none of
those, so the loop was exercised only by running the product.

`tests/replay.py` removes that constraint by replacing exactly one thing: the provider. The
`Agent` is real, tool dispatch is real, the hooks and the trace and the memory are real. The
model returns a recorded decision instead of an invented one, which is the only part of a
run that a test cannot afford to be nondeterministic.

This file proves the harness behaves — including the case that would make every future test
using it meaningless, a script that quietly repeats its last step while the loop runs away.
"""

from __future__ import annotations

import asyncio

import pytest

from agentevolver.model.types import accumulate_stream
from tests.replay import (
    Call,
    ScriptExhausted,
    Step,
    replaying,
    script_from_events,
)


def _accumulate(**request):
    """Fold one replayed stream exactly the way the loop does — through the manager."""
    from agentevolver.model import model_manager

    return asyncio.run(accumulate_stream(model_manager.stream(**request)))


# --------------------------------------------------------------------------- #
# What a replayed step produces
# --------------------------------------------------------------------------- #
def test_a_recorded_step_folds_into_the_decision_the_loop_reads():
    """The harness has to speak the provider's canonical event vocabulary, not its own.

    `accumulate_stream` is what the loop calls, so folding through it is the only proof
    that a replayed step is indistinguishable from a real one at the seam that matters.
    """
    script = [Step(reasoning="look at the file", tool_calls=[Call("read", {"path": "a.py"})])]

    with replaying(script):
        result = _accumulate(name="any", input={"messages": []})

    assert result["thinking"] == "look at the file"
    assert [(c.function.name if hasattr(c, "function") else c.name) for c in result["tool_calls"]]


def test_a_step_with_no_tool_calls_is_a_final_answer():
    """The shape of the last step of every successful run.

    A harness that always produced a tool call could never replay a run that finished.
    """
    with replaying([Step(reasoning="done, the answer is 4")]):
        result = _accumulate(name="any", input={"messages": []})

    assert result["tool_calls"] == []
    assert result["thinking"] == "done, the answer is 4"


def test_steps_are_returned_in_order_not_repeated():
    """Each call consumes the next step.

    Returning the same step forever is the tempting simplification, and it turns every
    replay test into an infinite loop that only a step budget stops.
    """
    script = [Step(reasoning="first"), Step(reasoning="second")]

    with replaying(script):
        first = _accumulate(name="any", input={"messages": []})
        second = _accumulate(name="any", input={"messages": []})

    assert (first["thinking"], second["thinking"]) == ("first", "second")


def test_asking_for_more_steps_than_the_script_has_is_an_error():
    """The failure that would make every test built on this meaningless.

    A loop that runs one step too many is exactly the defect a replay test should catch. If
    the harness answered it with an empty step, the run would end quietly and the test would
    pass — reporting the runaway as success.
    """
    with replaying([Step(reasoning="only one")]):
        _accumulate(name="any", input={"messages": []})

        with pytest.raises(ScriptExhausted, match="step 2"):
            _accumulate(name="any", input={"messages": []})


def test_the_harness_records_what_the_loop_sent():
    """Usually the thing under test.

    Asserting on the model's *answer* tests the script. Asserting on the request — the
    assembled messages, the tool schemas offered — tests the loop, which is the subject.
    """
    with replaying([Step(reasoning="ok")]) as model:
        _accumulate(name="my-model", input={"messages": [{"role": "user"}], "tools": []})

    assert model.consumed == 1
    assert model.calls[0]["name"] == "my-model"
    assert model.calls[0]["input"]["messages"] == [{"role": "user"}]


def test_recorded_usage_reaches_the_fold():
    """The loop reads usage off the stream; a script that omits it records zero tokens.

    A test about token accounting driven by such a script would be measuring the harness.
    """
    usage = {"input_tokens": 10, "output_tokens": 3}

    with replaying([Step(reasoning="x", usage=usage)]):
        result = _accumulate(name="any", input={"messages": []})

    assert result["usage"] == usage


# --------------------------------------------------------------------------- #
# Recovering a script from a recorded run
# --------------------------------------------------------------------------- #
def _agent_call(step: int, reasoning: str):
    from agentevolver.trace.types import TraceEvent, TraceEventType

    return TraceEvent(
        event_type=TraceEventType.AGENT_CALL, session_id="s", step_number=step, reasoning=reasoning
    )


def _tool_start(step: int, name: str, args: dict):
    from agentevolver.trace.types import TraceEvent, TraceEventType

    return TraceEvent(
        event_type=TraceEventType.TOOL_START,
        session_id="s",
        step_number=step,
        action_name=name,
        input=args,
    )


def test_a_recorded_run_becomes_a_script():
    """Any real run is a fixture. That is the property that makes this affordable.

    Without it every replay test needs a hand-written script, which is a second description
    of the model's behaviour that drifts from the first.
    """
    events = [
        _tool_start(1, "read", {"path": "a.py"}),
        _agent_call(1, "look at the file"),
        _agent_call(2, "that is the answer"),
    ]

    script = script_from_events(events)

    assert [step.reasoning for step in script] == ["look at the file", "that is the answer"]
    assert script[0].tool_calls[0].name == "read"
    assert script[0].tool_calls[0].args == {"path": "a.py"}
    assert script[1].tool_calls == []


def test_calls_are_attached_to_the_step_that_made_them():
    """Two steps, two tools; joining on step number is what keeps them apart.

    Attaching by document order instead would put both calls on the first step and leave
    the second looking like a final answer — a replay that silently ends early.
    """
    events = [
        _tool_start(1, "read", {}),
        _agent_call(1, "one"),
        _tool_start(2, "write", {}),
        _agent_call(2, "two"),
    ]

    script = script_from_events(events)

    assert [c.name for step in script for c in step.tool_calls] == ["read", "write"]
    assert len(script[0].tool_calls) == 1


def test_a_step_recorded_without_reasoning_still_replays():
    """Runs recorded before a field existed are still usable fixtures.

    Dropping such a step would silently shorten the script, and the replay would end one
    step early for a reason no assertion could name.
    """
    from agentevolver.trace.types import TraceEvent, TraceEventType

    script = script_from_events(
        [
            TraceEvent(event_type=TraceEventType.AGENT_CALL, session_id="s", step_number=1),
        ]
    )

    assert len(script) == 1
    assert script[0].reasoning == ""


def test_events_that_are_not_decisions_are_ignored():
    """A trace holds far more than the model's turns.

    Treating any of it as a step would insert decisions the model never made.
    """
    from agentevolver.trace.types import TraceEvent, TraceEventType

    events = [
        TraceEvent(event_type=TraceEventType.AGENT_START, session_id="s"),
        _agent_call(1, "the only decision"),
        TraceEvent(event_type=TraceEventType.TOOL_CALL, session_id="s", step_number=1),
        TraceEvent(event_type=TraceEventType.AGENT_END, session_id="s"),
    ]

    assert len(script_from_events(events)) == 1


# --------------------------------------------------------------------------- #
# What is still needed to drive the whole loop
# --------------------------------------------------------------------------- #