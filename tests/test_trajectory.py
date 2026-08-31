"""A trajectory is what a run looks like to a trainer, and it is assembled from hooks.

Nothing calls this module directly. Steps accumulate from lifecycle hook events fired by
the agent loop — an observation per action, a close per step, a finalize at the end — so
every ordering assumption here is really an assumption about hooks that fire elsewhere.
There is no PRE_STEP wiring at all, which is why a step has to materialize from its first
observation rather than being opened for it.

Two properties make the result trainable rather than merely recorded. The SFT record has
to be byte-faithful to what the model emits at inference: reasoning as ``content``, native
``tool_calls`` with JSON-string arguments. Training on any other assistant shape teaches
the model an output schema the agent does not parse. And the reward arrives *after* the
run returns — a benchmark judge scores the final output — so backfill has to reach every
step of an already-finalized, already-written trajectory and re-persist it.
"""

import json
from unittest.mock import patch

import pytest

from agentevolver.message import AssistantMessage, Function, HumanMessage, ToolCall
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.trajectory.server import TrajectoryManagerServer
from agentevolver.trajectory.types import (
    SFT_EXPORT_VERSION,
    TRAJECTORY_SCHEMA_VERSION,
    Trajectory,
    TrajectoryContext,
    TrajectoryStep,
)

TASK = "task-1"
SESSION = "session-1"


@pytest.fixture
def trajectories(tmp_path):
    manager = TrajectoryManagerServer()
    manager.base_dir = str(tmp_path / "trajectory")
    return manager


def ctx(**input_):
    return TrajectoryContext(id=SESSION, task_id=TASK, agent_name="code_agent", input=input_)


def a_recorded_run(manager, *, steps=1):
    """Drive the manager through the hook sequence a real run produces.

    One observation and one close per step, in the order the agent loop fires them —
    tests that build a trajectory any other way would be describing a sequence that never
    happens.
    """
    manager.begin(ctx(task="Fix the bug"))
    for n in range(steps):
        manager.add_observation(
            ctx(
                step_number=n,
                action={"index": 0, "type": "tool", "name": "bash", "args": {"cmd": "ls"}},
                action_result="a.txt",
            )
        )
        manager.close_step(
            ctx(
                step_number=n,
                messages=[HumanMessage(content=f"step {n}")],
                reasoning=f"thinking {n}",
                plan=[{"id": "call_0", "name": "bash", "args": {"cmd": "ls"}}],
                step_tokens=100 + n,
            )
        )
    return manager


# --------------------------------------------------------------------------- #
# Assembling a run from its hook events
# --------------------------------------------------------------------------- #
def test_a_run_accumulates_its_steps_in_order(trajectories):
    """Step order is the training signal; a set of steps in the wrong order is a wrong episode."""
    a_recorded_run(trajectories, steps=3)
    traj = trajectories.get(TASK)
    assert [s.step_number for s in traj.steps] == [0, 1, 2]
    assert traj.task_description == "Fix the bug"
    assert traj.agent_name == "code_agent"


def test_a_step_carries_the_prompt_decision_and_observations(trajectories):
    """All four parts of ``(z, a, o, r)`` come from different hooks and must land together.

    They are written by two separate calls — the observation and the close — so a step
    that keeps one and drops the other still looks like a step. ``token_usage`` is checked
    here because it is the field the reward pipeline reads.
    """
    a_recorded_run(trajectories)
    step = trajectories.get(TASK).steps[0]
    assert step.reasoning == "thinking 0"
    assert step.actions[0]["name"] == "bash"
    assert step.observations[0]["result"] == "a.txt"
    assert step.token_usage == 100


def test_a_step_cites_the_trace_request_and_event_range_it_projects(trajectories):
    """Training provenance must point back to facts instead of copying request metadata.

    Retry and fallback can put several model requests in one step. The last request is
    the route that produced the decision, while the inclusive range names all evidence
    — request, actions, results, and the closing assistant turn — used by the projection.
    """
    events = [
        TraceEvent(
            event_type=TraceEventType.MODEL_REQUEST,
            session_id=SESSION,
            task_id=TASK,
            step_number=0,
            seq_no=4,
            metadata={"request_snapshot_id": "primary"},
        ),
        TraceEvent(
            event_type=TraceEventType.MODEL_REQUEST,
            session_id=SESSION,
            task_id=TASK,
            step_number=0,
            seq_no=5,
            metadata={"request_snapshot_id": "fallback"},
        ),
        TraceEvent(
            event_type=TraceEventType.TOOL_CALL,
            session_id=SESSION,
            task_id=TASK,
            step_number=0,
            seq_no=8,
        ),
        TraceEvent(
            event_type=TraceEventType.AGENT_CALL,
            session_id=SESSION,
            task_id=TASK,
            step_number=0,
            seq_no=9,
        ),
    ]
    trajectories.begin(ctx(task="t"))
    with patch("agentevolver.trace.server.trace_manager.events", return_value=events):
        trajectories.close_step(ctx(step_number=0))

    step = trajectories.get(TASK).steps[0]
    assert step.request_snapshot_id == "fallback"
    assert (step.source_trace_seq_start, step.source_trace_seq_end) == (4, 9)


def test_a_step_materializes_from_its_first_observation(trajectories):
    """Nothing wires a PRE_STEP event; the step must appear lazily.

    Depending on an opening hook that does not exist would drop the first action of every
    step, and the loss is invisible: the step is still there, just short one observation.
    """
    trajectories.begin(ctx(task="t"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}, action_result="out"))
    trajectories.close_step(ctx(step_number=0))
    assert len(trajectories.get(TASK).steps[0].observations) == 1


def test_several_actions_in_one_step_are_all_observed(trajectories):
    """A turn dispatches its actions as a batch — every result belongs to that step.

    The open step is keyed by step number, so a second observation must join the existing
    step rather than replace it. Replacing would keep only the last action of every
    parallel turn, and batched turns are the normal case.
    """
    trajectories.begin(ctx(task="t"))
    for i in range(3):
        trajectories.add_observation(
            ctx(
                step_number=0,
                action={"index": i, "name": "bash"},
                action_result=f"out{i}",
            )
        )
    trajectories.close_step(ctx(step_number=0))
    assert [o["index"] for o in trajectories.get(TASK).steps[0].observations] == [0, 1, 2]


def test_an_action_error_is_recorded_beside_its_action(trajectories):
    """A failed action is training data, not a hole in the episode.

    The error goes into the observation next to the action that produced it; dropping it
    would leave a step whose action apparently returned nothing, which is indistinguishable
    from a tool that legitimately produced no output.
    """
    trajectories.begin(ctx(task="t"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}, error="exit 1"))
    trajectories.close_step(ctx(step_number=0))
    assert trajectories.get(TASK).steps[0].observations[0]["error"] == "exit 1"


def test_events_for_an_unopened_run_are_ignored_not_fatal(trajectories):
    """Hooks can fire out of order; recording must never break the run.

    Trajectory capture is bookkeeping alongside the agent, not part of it. A stray
    observation for a task that was never begun — a late hook, a run whose start was
    filtered — has to be dropped rather than raised, because the exception would surface
    inside the agent's own loop.
    """
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}))
    trajectories.close_step(ctx(step_number=0))
    trajectories.finalize(ctx(success=True))
    assert trajectories.get(TASK) is None


def test_beginning_a_run_again_discards_the_previous_open_step(trajectories):
    """A second ``begin`` on one task id is a new run, not a continuation.

    The open step belongs to the run that opened it. Carrying it over would splice the
    first run's half-finished step onto the front of the second — an episode with an
    action from one task and a result from another, which is worse than a lost step
    because it trains on something that never happened.
    """
    trajectories.begin(ctx(task="first"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}))
    trajectories.begin(ctx(task="second"))
    trajectories.close_step(ctx(step_number=0))
    assert trajectories.get(TASK).task_description == "second"
    assert trajectories.get(TASK).steps[0].observations == []


def test_causal_metadata_is_kept_only_when_present(trajectories):
    """A sub-agent's parent link is recorded; a top-level run gets no empty placeholder.

    Writing ``{"parent_session_id": None}`` for every root run would make "has no parent"
    and "parent unknown" the same value, and the multi-agent tree is reconstructed from
    exactly these keys.
    """
    trajectories.begin(ctx(task="t", parent_session_id="parent-1"))
    assert trajectories.get(TASK).metadata == {"parent_session_id": "parent-1"}
    trajectories.begin(ctx(task="t"))
    assert trajectories.get(TASK).metadata == {}


# --------------------------------------------------------------------------- #
# Finalizing and writing it down
# --------------------------------------------------------------------------- #
def test_finalizing_records_the_outcome_and_writes_the_file(trajectories):
    """The file is JSONL so a step can be streamed without loading the run.

    That only holds if the header stays a header: ``steps`` must not also be nested inside
    line one, or every step is on disk twice and a reader that trusts the header disagrees
    with one that reads the lines.
    """
    a_recorded_run(trajectories, steps=2)
    trajectories.finalize(ctx(success=True, result="done"))
    traj = trajectories.get(TASK)
    assert traj.success is True
    assert traj.final_result == "done"

    lines = open(trajectories._path(traj), encoding="utf-8").read().splitlines()
    header = json.loads(lines[0])
    assert header["__header__"] is True
    assert header["task_id"] == TASK
    assert header["schema_version"] == TRAJECTORY_SCHEMA_VERSION
    assert "steps" not in header  # steps are their own lines
    assert len(lines) == 3  # header + two steps


def test_a_non_string_result_is_stringified_rather_than_dropped(trajectories):
    """An agent may return a dict; the field is typed as a string and the file is JSON.

    Coercing keeps the result readable. The alternative that looks tidier — store only
    strings and discard anything else — loses the run's answer in exactly the cases where
    the agent produced structured output.
    """
    a_recorded_run(trajectories)
    trajectories.finalize(ctx(success=True, result={"files": 3}))
    assert trajectories.get(TASK).final_result == "{'files': 3}"


def test_a_task_id_with_slashes_stays_inside_the_trajectory_directory(trajectories):
    """Task ids come from callers and benchmarks, and are used to build a filename.

    A slash would make the id a path fragment, so ``a/../b`` writes outside the trajectory
    directory — into the source tree, for a run started from a checkout.
    """
    traj = Trajectory(session_id=SESSION, task_id="a/../b")
    assert "/" not in trajectories._path(traj).rsplit("/", 1)[1]


def test_a_future_trajectory_schema_is_refused_instead_of_partly_loaded(trajectories, tmp_path):
    """Parseable fields do not prove semantic compatibility for a training sample.

    A future writer may change reward or action meaning while retaining valid JSON. An
    old reader that loads the familiar fields would silently train on a projection it
    does not understand, so refusing the whole file is the only safe default.
    """
    path = tmp_path / "future.jsonl"
    path.write_text(
        json.dumps(
            {
                "__header__": True,
                "schema_version": 999,
                "session_id": SESSION,
                "task_id": TASK,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert trajectories.load(str(path)) is None


# --------------------------------------------------------------------------- #
# The reward that lands after the run
# --------------------------------------------------------------------------- #
def test_a_late_reward_reaches_every_step(trajectories):
    """The judge scores the output after the agent has already returned.

    The trajectory is finalized and written by then, so backfill has to reopen it and
    reach every step. A reward that stopped at the trajectory header would leave every
    step at ``0.0`` — which is a valid reward, so nothing downstream would flag it.
    """
    a_recorded_run(trajectories, steps=3)
    trajectories.finalize(ctx(success=True, result="done"))
    trajectories.set_reward(TASK, 0.75)
    traj = trajectories.get(TASK)
    assert traj.reward == 0.75
    assert [s.reward for s in traj.steps] == [0.75, 0.75, 0.75]


def test_a_late_reward_is_re_persisted(trajectories):
    """In-memory backfill is not enough: the file was already written without the reward.

    Whatever reads these files later reads the file, not this process's memory, so a
    backfill that skips the rewrite produces training data that is silently unscored.
    """
    a_recorded_run(trajectories)
    trajectories.finalize(ctx(success=True))
    trajectories.set_reward(TASK, 1.0)
    header = json.loads(open(trajectories._path(trajectories.get(TASK))).readline())
    assert header["reward"] == 1.0


def test_a_reward_for_an_unknown_run_is_ignored(trajectories):
    """Scoring happens in the driver, which may outlive or precede the run it scores."""
    trajectories.set_reward("never-ran", 1.0)  # must not raise


def test_a_reward_can_be_addressed_by_session_when_the_task_id_is_unknown(trajectories):
    """A benchmark holds the session id it started, not the run's internal task id.

    The count is returned rather than a bare success flag so the caller can tell "scored
    one run" from "matched nothing and did nothing".
    """
    a_recorded_run(trajectories)
    assert trajectories.set_reward_by_session(SESSION, 0.5) == 1
    assert trajectories.get(TASK).reward == 0.5


def test_addressing_an_unknown_session_matches_nothing(trajectories):
    """Zero, not an error — a driver scoring a batch must not stop at the first miss."""
    assert trajectories.set_reward_by_session("no-such-session", 1.0) == 0


# --------------------------------------------------------------------------- #
# The shape the trainer is handed
# --------------------------------------------------------------------------- #
def test_an_sft_record_ends_with_a_native_tool_calling_turn(trajectories):
    """The target must match what the model emits at inference.

    The agent runs in native tool-calling mode: reasoning as ``content``, calls as
    ``tool_calls`` with ``arguments`` a JSON *string*. Any other projection — the call
    rendered into the text, or arguments left as a dict — teaches a schema the agent's own
    parser does not read, and the model gets worse at the only format it is asked to
    produce.
    """
    a_recorded_run(trajectories)
    record = trajectories.export_sft(TASK)[0]
    assistant = record["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "thinking 0"
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "bash"
    assert json.loads(call["function"]["arguments"]) == {"cmd": "ls"}


def test_an_export_names_its_schema_and_source_trace_lineage():
    """A dataset row without exporter and source versions cannot be reproduced later."""
    trajectory = Trajectory(
        session_id=SESSION,
        task_id=TASK,
        source_trace_seq_start=1,
        source_trace_seq_end=12,
        steps=[
            TrajectoryStep(
                step_number=0,
                request_snapshot_id="sha256:request",
                source_trace_seq_start=3,
                source_trace_seq_end=10,
            )
        ],
    )
    provenance = trajectory.to_sft_records()[0]["provenance"]
    assert provenance["trajectory_schema_version"] == TRAJECTORY_SCHEMA_VERSION
    assert provenance["export_version"] == SFT_EXPORT_VERSION
    assert provenance["request_snapshot_id"] == "sha256:request"
    assert provenance["trajectory_source_trace_seq_end"] == 12


def test_arguments_already_serialized_are_left_alone():
    """Some providers hand back the arguments as a string already.

    Serializing a second time would produce a JSON string *of* a JSON string — still valid
    JSON, still a string where one was expected, and wrong only once something tries to
    parse it into arguments.
    """
    step = TrajectoryStep(step_number=0, actions=[{"name": "bash", "args": '{"cmd":"ls"}'}])
    assert step._assistant_message()["tool_calls"][0]["function"]["arguments"] == '{"cmd":"ls"}'


def test_an_action_without_an_id_is_given_a_positional_one():
    """Chat format requires an id per call, and two calls in a turn must not share one.

    Actions recorded from a provider that does not issue ids arrive without them; falling
    back to a constant would pair both results to the same call.
    """
    step = TrajectoryStep(step_number=0, actions=[{"name": "a"}, {"name": "b"}])
    ids = [c["id"] for c in step._assistant_message()["tool_calls"]]
    assert ids == ["call_0", "call_1"]


def test_a_step_with_no_actions_emits_a_plain_assistant_turn():
    """``tool_calls: []`` is a different training signal from no key at all.

    An empty list is an assistant turn that considered calling something and chose not to;
    an absent key is a turn that was never about calling anything. A step that only
    reasoned is the second.
    """
    assert (
        "tool_calls"
        not in TrajectoryStep(step_number=0, reasoning="just thinking")._assistant_message()
    )


def test_prompt_messages_survive_as_chat_dicts(trajectories):
    """``z_t`` is the prompt actually sent, and it has to come out as plain chat.

    The stored messages are pydantic objects held as ``Any`` to avoid being downcast; the
    export is where they become JSON, and a message that serialized to its repr would make
    the record unusable while still being a well-formed dict.
    """
    a_recorded_run(trajectories)
    first = trajectories.export_sft(TASK)[0]["messages"][0]
    assert first == {"role": "user", "content": "step 0"}


def test_an_assistant_prompt_message_keeps_its_tool_calls():
    """A prior turn in the prompt carries calls too, and the id is what pairs it to a result.

    Losing it here breaks the prompt rather than the target: the record then shows a
    tool result answering a call that is not in the history.
    """
    step = TrajectoryStep(
        step_number=0,
        messages_sent=[
            AssistantMessage(
                content="calling",
                tool_calls=[
                    ToolCall(id="c1", function=Function(name="bash", arguments="{}")),
                ],
            ),
        ],
    )
    assert step.to_sft_record()["messages"][0]["tool_calls"][0]["id"] == "c1"


def test_a_plain_dict_prompt_message_passes_through_untouched():
    """Odd input must not crash the trajectory.

    Not everything on the prompt path is a ``Message`` — some callers assemble dicts
    directly — and a run must not be lost because its recording could not classify one.
    """
    step = TrajectoryStep(step_number=0, messages_sent=[{"role": "system", "content": "s"}])
    assert step.to_sft_record()["messages"][0] == {"role": "system", "content": "s"}


def test_exports_for_an_unknown_run_are_empty_not_an_error(trajectories):
    """Export is driven by a task id from outside; a miss is normal, not exceptional.

    The RL side is passed a deliberately useless format object: if the lookup were done
    after delegating, this call would raise instead of returning empty.
    """
    assert trajectories.export_sft("never-ran") == []
    assert trajectories.export_rl("never-ran", object()) == []


def test_rl_export_delegates_to_the_supplied_format(trajectories):
    """The RL shape belongs to the training framework, not to this module.

    Keeping VERL and the rest behind a protocol is what stops the core from importing a
    trainer. The fake format asserts only that the whole trajectory was handed over — if
    this module started reshaping episodes itself, adding a second framework would mean
    editing it.
    """
    a_recorded_run(trajectories)

    class FakeFormat:
        name = "fake"

        def to_episode(self, trajectory):
            return [{"task": trajectory.task_id, "steps": len(trajectory.steps)}]

    assert trajectories.export_rl(TASK, FakeFormat()) == [{"task": TASK, "steps": 1}]
