"""The shipped `ralph` preset compiles, starts a fresh worker each round, and stops on the sentinel.

Ralph is a preset workflow document, not an engine: everything that makes it Ralph is
attributes and prompt text inside `agentevolver/workflow/default/ralph.html`. That makes it
easy to break silently. Nothing else in the test suite compiles the shipped built-ins, so a
typo in the loop condition, a step id renamed out from under `until`, or a `max-rounds` above
the compiler's ceiling would first surface as a workflow that registers at startup and then
either fails on its first run or never terminates. The two behavioural tests pin the parts a
reader cannot verify by eye: that round N+1 is a new agent call rather than a continuation,
and that the loop's exit is driven by the sentinel check's exit code and not by round count.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentevolver.response.types import Response, ResponseType
from agentevolver.workflow import WorkflowState, workflow_compiler

from tests.test_workflow_runtime import FakeRuntime


RALPH = Path(__file__).parents[1] / "agentevolver" / "workflow" / "default" / "ralph.html"


def _definition():
    return workflow_compiler.compile_file(str(RALPH))


def _agent_response(message="did a piece of work"):
    """What `agent_manager` hands back: `_normalize` turns this into {message, data, files}."""
    return Response(type=ResponseType.AGENT, success=True, message=message,
                    data={"done": True, "result": message, "step": 3})


def _bash_response(exit_code):
    """What `bash_tool` hands back. `test` reports 0 when the sentinel file exists."""
    return Response(type=ResponseType.TOOL, success=True, message="",
                    data={"exit_code": exit_code, "command": "test -f .ralph/COMPLETE"})


# --------------------------------------------------------------------------- #
# The document itself
# --------------------------------------------------------------------------- #
def test_the_shipped_ralph_document_compiles():
    """Built-ins are compiled at startup, where a failure is a log line, not a red test.

    `WorkflowContextManager.initialize()` globs `default/*.html`, so a document that no
    longer compiles simply does not register — the model's roster is one workflow shorter
    and nothing says why.
    """
    definition = _definition()
    assert definition.name == "ralph"
    assert list(definition.inputs) == ["objective", "max_rounds"]


def test_the_only_thing_a_caller_chooses_is_the_objective_and_the_cap():
    """Ralph's contract is that the loop is fixed and the caller supplies data only.

    An extra input would be a second lever on the orchestration — a caller able to swap the
    worker agent or rewrite the round prompt is no longer running the preset, and the
    reviewed document stops describing what ran.
    """
    definition = _definition()
    assert definition.inputs["objective"].required is True
    assert definition.inputs["max_rounds"].required is False
    assert definition.inputs["max_rounds"].default == 8


def test_the_loop_exits_on_the_sentinel_check_rather_than_the_worker_step():
    """`until` names the check step, and the sense of the test is easy to invert.

    `test -f` exits 0 when the file is there, so completion is exit code *zero* — the
    falsy value. The condition therefore has to be negated, and an author who reads
    "exit code" as "did it fail" writes it the other way round and gets a loop that stops
    after one round, every time, which looks like a working workflow in a short run.
    """
    loop = _definition().program[0]
    assert loop.condition == "not stop.data.exit_code"
    assert loop.condition_mode == "until"
    assert [child.id for child in loop.children] == ["worker", "stop"]


def test_the_round_ceiling_stays_inside_what_the_compiler_accepts():
    """`max-rounds` is capped at 100 by the compiler and is a compile-time attribute.

    It cannot be driven by an input, which is why the caller's cap is enforced by the
    in-loop check instead. Raising this number past the compiler's ceiling would reject
    the document at startup.
    """
    assert _definition().program[0].max_rounds == 20


# --------------------------------------------------------------------------- #
# What the runtime actually does with it
# --------------------------------------------------------------------------- #
def test_every_round_is_a_separate_agent_call(tmp_path):
    """The whole point of Ralph: round N+1 must not continue round N's conversation.

    A single long-lived agent would produce one `_invoke` for the agent step no matter how
    many rounds ran. Counting the calls is the only observable difference between "iterated
    with a fresh worker" and "iterated inside one worker's context", and the two are
    indistinguishable from the workflow's output.
    """
    # The fake stands in for the shell, so it reports "not complete" forever and the
    # `-ge` half of the real command never fires: the run goes the document's full 20.
    def handler(target, task, args):
        return _agent_response() if target == "general_agent" else _bash_response(1)

    runtime = FakeRuntime(handler)
    run = asyncio.run(runtime.run(
        _definition(),
        input={"objective": "make the build green"},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    ))

    assert run.state == WorkflowState.SUCCEEDED
    agent_calls = [call for call in runtime.calls if call[0] == "general_agent"]
    assert len(agent_calls) == 20


def test_a_zero_exit_from_the_sentinel_check_ends_the_run(tmp_path):
    """Completion has to be able to arrive before the round ceiling, or the cap is the only exit.

    `until` is evaluated after the round body, so the round that reports completion still
    runs in full — the assertion is that the round *after* it does not.
    """
    rounds = {"n": 0}

    def handler(target, task, args):
        if target == "general_agent":
            rounds["n"] += 1
            return _agent_response()
        return _bash_response(0 if rounds["n"] >= 2 else 1)

    runtime = FakeRuntime(handler)
    run = asyncio.run(runtime.run(
        _definition(),
        input={"objective": "make the build green"},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    ))

    assert run.state == WorkflowState.SUCCEEDED
    assert rounds["n"] == 2
    assert len(run.output["rounds"]) == 2


def test_the_worker_prompt_carries_the_objective_verbatim_every_round(tmp_path):
    """"Immutable objective" means the same words reach round 5 as reached round 1.

    The prompt is assembled from `${inputs.objective}` inside the loop body, so a scope bug
    that let a round's own output shadow the input would rewrite the goal mid-run — the
    failure Ralph exists to prevent, and one that produces plausible-looking rounds.
    """
    objective = "make the build green without touching the public API"

    def handler(target, task, args):
        if target == "general_agent":
            return _agent_response()
        return _bash_response(0 if len([c for c in runtime.calls if c[0] == "general_agent"]) >= 3 else 1)

    runtime = FakeRuntime(handler)
    asyncio.run(runtime.run(
        _definition(),
        input={"objective": objective},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    ))

    prompts = [task for target, task, _ in runtime.calls if target == "general_agent"]
    assert len(prompts) == 3
    assert all(objective in prompt for prompt in prompts)
    # The round number is the one thing that legitimately differs between rounds.
    assert "round 1." in prompts[0] and "round 3." in prompts[2]


def test_the_callers_cap_is_enforced_by_the_check_step_not_the_document(tmp_path):
    """`max-rounds` cannot read an input, so the cap lives in the shell test's second clause.

    That makes the cap invisible in the compiled program — it is a string inside a tool
    argument. If the argument stops interpolating `${inputs.max_rounds}`, the cap silently
    becomes the document's ceiling of 20 and a caller asking for 3 rounds pays for 20.
    """
    def handler(target, task, args):
        if target == "bash_tool":
            handler.commands.append(args["command"])
        return _agent_response() if target == "general_agent" else _bash_response(1)
    handler.commands = []

    runtime = FakeRuntime(handler)
    asyncio.run(runtime.run(
        _definition(),
        input={"objective": "finish the migration", "max_rounds": 5},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    ))

    assert handler.commands[0] == "test -f .ralph/COMPLETE || test 1 -ge 5"
    assert handler.commands[4] == "test -f .ralph/COMPLETE || test 5 -ge 5"


def test_a_cap_outside_the_declared_range_is_refused_before_any_agent_starts(tmp_path):
    """The cap is interpolated into a shell command, so it must be a validated integer.

    `max_rounds` reaches `bash_tool` as text. Input validation is what keeps it an integer
    between 1 and 20 rather than an arbitrary string a caller chose, and it has to reject
    before the first round rather than after N agents have already run.
    """
    runtime = FakeRuntime(lambda *_: _agent_response())
    run = asyncio.run(runtime.run(
        _definition(),
        input={"objective": "finish the migration", "max_rounds": 99},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    ))

    assert run.state == WorkflowState.REJECTED
    assert runtime.calls == []
