"""Trajectory: the trainable projection of a run, and the reward that lands late.

Two things make this module load-bearing. The SFT record has to be byte-faithful
to what the model emits at inference — training on a different assistant shape
teaches a different output schema than the agent uses. And rewards arrive *after*
the run returns (a benchmark judge scores the output), so backfill has to reach
every step of an already-finalized trajectory.
"""

import json

import pytest

from agentevolver.message import AssistantMessage, Function, HumanMessage, ToolCall
from agentevolver.trajectory.server import TrajectoryManagerServer
from agentevolver.trajectory.types import Trajectory, TrajectoryContext, TrajectoryStep

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
    manager.begin(ctx(task="Fix the bug"))
    for n in range(steps):
        manager.add_observation(ctx(
            step_number=n,
            action={"index": 0, "type": "tool", "name": "bash", "args": {"cmd": "ls"}},
            action_result="a.txt",
        ))
        manager.close_step(ctx(
            step_number=n,
            messages=[HumanMessage(content=f"step {n}")],
            reasoning=f"thinking {n}",
            plan=[{"id": "call_0", "name": "bash", "args": {"cmd": "ls"}}],
            step_tokens=100 + n,
        ))
    return manager


# ------------------------------------------------------------------ building
def test_a_run_accumulates_its_steps_in_order(trajectories):
    a_recorded_run(trajectories, steps=3)
    traj = trajectories.get(TASK)
    assert [s.step_number for s in traj.steps] == [0, 1, 2]
    assert traj.task_description == "Fix the bug"
    assert traj.agent_name == "code_agent"


def test_a_step_carries_the_prompt_decision_and_observations(trajectories):
    a_recorded_run(trajectories)
    step = trajectories.get(TASK).steps[0]
    assert step.reasoning == "thinking 0"
    assert step.actions[0]["name"] == "bash"
    assert step.observations[0]["result"] == "a.txt"
    assert step.token_usage == 100


def test_a_step_materializes_from_its_first_observation(trajectories):
    """Nothing wires a PRE_STEP event; the step must appear lazily."""
    trajectories.begin(ctx(task="t"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}, action_result="out"))
    trajectories.close_step(ctx(step_number=0))
    assert len(trajectories.get(TASK).steps[0].observations) == 1


def test_several_actions_in_one_step_are_all_observed(trajectories):
    """A turn dispatches its actions as a batch — every result belongs to that step."""
    trajectories.begin(ctx(task="t"))
    for i in range(3):
        trajectories.add_observation(ctx(
            step_number=0, action={"index": i, "name": "bash"}, action_result=f"out{i}",
        ))
    trajectories.close_step(ctx(step_number=0))
    assert [o["index"] for o in trajectories.get(TASK).steps[0].observations] == [0, 1, 2]


def test_an_action_error_is_recorded_beside_its_action(trajectories):
    trajectories.begin(ctx(task="t"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}, error="exit 1"))
    trajectories.close_step(ctx(step_number=0))
    assert trajectories.get(TASK).steps[0].observations[0]["error"] == "exit 1"


def test_events_for_an_unopened_run_are_ignored_not_fatal(trajectories):
    """Hooks can fire out of order; recording must never break the run."""
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}))
    trajectories.close_step(ctx(step_number=0))
    trajectories.finalize(ctx(success=True))
    assert trajectories.get(TASK) is None


def test_beginning_a_run_again_discards_the_previous_open_step(trajectories):
    trajectories.begin(ctx(task="first"))
    trajectories.add_observation(ctx(step_number=0, action={"name": "bash"}))
    trajectories.begin(ctx(task="second"))
    trajectories.close_step(ctx(step_number=0))
    assert trajectories.get(TASK).task_description == "second"
    assert trajectories.get(TASK).steps[0].observations == []


def test_causal_metadata_is_kept_only_when_present(trajectories):
    trajectories.begin(ctx(task="t", parent_session_id="parent-1"))
    assert trajectories.get(TASK).metadata == {"parent_session_id": "parent-1"}
    trajectories.begin(ctx(task="t"))
    assert trajectories.get(TASK).metadata == {}


# ------------------------------------------------------------------ finalize
def test_finalizing_records_the_outcome_and_writes_the_file(trajectories):
    a_recorded_run(trajectories, steps=2)
    trajectories.finalize(ctx(success=True, result="done"))
    traj = trajectories.get(TASK)
    assert traj.success is True
    assert traj.final_result == "done"

    lines = open(trajectories._path(traj), encoding="utf-8").read().splitlines()
    header = json.loads(lines[0])
    assert header["__header__"] is True
    assert header["task_id"] == TASK
    assert "steps" not in header          # steps are their own lines
    assert len(lines) == 3                # header + two steps


def test_a_non_string_result_is_stringified_rather_than_dropped(trajectories):
    a_recorded_run(trajectories)
    trajectories.finalize(ctx(success=True, result={"files": 3}))
    assert trajectories.get(TASK).final_result == "{'files': 3}"


def test_a_task_id_with_slashes_stays_inside_the_trajectory_directory(trajectories):
    traj = Trajectory(session_id=SESSION, task_id="a/../b")
    assert "/" not in trajectories._path(traj).rsplit("/", 1)[1]


# -------------------------------------------------------------------- reward
def test_a_late_reward_reaches_every_step(trajectories):
    """The judge scores the output after the agent has already returned."""
    a_recorded_run(trajectories, steps=3)
    trajectories.finalize(ctx(success=True, result="done"))
    trajectories.set_reward(TASK, 0.75)
    traj = trajectories.get(TASK)
    assert traj.reward == 0.75
    assert [s.reward for s in traj.steps] == [0.75, 0.75, 0.75]


def test_a_late_reward_is_re_persisted(trajectories):
    a_recorded_run(trajectories)
    trajectories.finalize(ctx(success=True))
    trajectories.set_reward(TASK, 1.0)
    header = json.loads(open(trajectories._path(trajectories.get(TASK))).readline())
    assert header["reward"] == 1.0


def test_a_reward_for_an_unknown_run_is_ignored(trajectories):
    trajectories.set_reward("never-ran", 1.0)  # must not raise


def test_a_reward_can_be_addressed_by_session_when_the_task_id_is_unknown(trajectories):
    a_recorded_run(trajectories)
    assert trajectories.set_reward_by_session(SESSION, 0.5) == 1
    assert trajectories.get(TASK).reward == 0.5


def test_addressing_an_unknown_session_matches_nothing(trajectories):
    assert trajectories.set_reward_by_session("no-such-session", 1.0) == 0


# -------------------------------------------------------------------- export
def test_an_sft_record_ends_with_a_native_tool_calling_turn(trajectories):
    """The target must match what the model emits at inference, or SFT teaches
    a schema the agent does not use."""
    a_recorded_run(trajectories)
    record = trajectories.export_sft(TASK)[0]
    assistant = record["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "thinking 0"
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "bash"
    assert json.loads(call["function"]["arguments"]) == {"cmd": "ls"}


def test_arguments_already_serialized_are_left_alone():
    step = TrajectoryStep(step_number=0, actions=[{"name": "bash", "args": '{"cmd":"ls"}'}])
    assert step._assistant_message()["tool_calls"][0]["function"]["arguments"] == '{"cmd":"ls"}'


def test_an_action_without_an_id_is_given_a_positional_one():
    step = TrajectoryStep(step_number=0, actions=[{"name": "a"}, {"name": "b"}])
    ids = [c["id"] for c in step._assistant_message()["tool_calls"]]
    assert ids == ["call_0", "call_1"]


def test_a_step_with_no_actions_emits_a_plain_assistant_turn():
    """``tool_calls: []`` is a different training signal from no key at all."""
    assert "tool_calls" not in TrajectoryStep(step_number=0, reasoning="just thinking")._assistant_message()


def test_prompt_messages_survive_as_chat_dicts(trajectories):
    a_recorded_run(trajectories)
    first = trajectories.export_sft(TASK)[0]["messages"][0]
    assert first == {"role": "user", "content": "step 0"}


def test_an_assistant_prompt_message_keeps_its_tool_calls():
    step = TrajectoryStep(step_number=0, messages_sent=[
        AssistantMessage(content="calling", tool_calls=[
            ToolCall(id="c1", function=Function(name="bash", arguments="{}")),
        ]),
    ])
    assert step.to_sft_record()["messages"][0]["tool_calls"][0]["id"] == "c1"


def test_a_plain_dict_prompt_message_passes_through_untouched():
    """Odd input must not crash the trajectory."""
    step = TrajectoryStep(step_number=0, messages_sent=[{"role": "system", "content": "s"}])
    assert step.to_sft_record()["messages"][0] == {"role": "system", "content": "s"}


def test_exports_for_an_unknown_run_are_empty_not_an_error(trajectories):
    assert trajectories.export_sft("never-ran") == []
    assert trajectories.export_rl("never-ran", object()) == []


def test_rl_export_delegates_to_the_supplied_format(trajectories):
    a_recorded_run(trajectories)

    class FakeFormat:
        name = "fake"

        def to_episode(self, trajectory):
            return [{"task": trajectory.task_id, "steps": len(trajectory.steps)}]

    assert trajectories.export_rl(TASK, FakeFormat()) == [{"task": TASK, "steps": 1}]
