"""A trajectory can be rebuilt from trace facts and append-only reward labels.

The live trajectory hook is convenient but it is a second writer observing the same run.
If it misses one callback, its JSONL remains valid and silently disagrees with trace. These
tests establish the independent correctness path: delete the cache, replay facts, and
recover the prompt, action, observation, outcome, reward, and provenance deterministically.
"""

from datetime import datetime, timezone

import pytest

from agentevolver.trace.types import (
    agent_call_event,
    agent_end_event,
    agent_start_event,
    tool_call_event,
    tool_start_event,
    TraceEvent,
    TraceEventType,
)
from agentevolver.trajectory.labels import RewardLabel, UnsupportedRewardLabel
from agentevolver.trajectory.projector import PROJECTOR_VERSION, project_trajectory
from agentevolver.trajectory.server import TrajectoryManagerServer


SESSION = "session-1"
TASK = "task-1"
AGENT = "code_agent"


def _with_seq(event, seq):
    event.seq_no = seq
    return event


def _facts():
    """One complete step in the exact order the live trace writes it."""
    return [
        _with_seq(agent_start_event(SESSION, TASK, AGENT, "Fix it"), 0),
        _with_seq(TraceEvent(
            event_type=TraceEventType.MODEL_REQUEST,
            session_id=SESSION,
            task_id=TASK,
            agent_name=AGENT,
            step_number=0,
            input={
                "snapshot_id": "sha256:request",
                "messages": [{"role": "user", "content": "Fix it"}],
                "tools": [{"type": "function", "function": {"name": "bash"}}],
            },
            metadata={"request_snapshot_id": "sha256:request"},
        ), 1),
        _with_seq(tool_start_event(
            SESSION, TASK, AGENT, 0, 0, "bash", {"cmd": "pytest"}, call_id="call-1",
        ), 2),
        _with_seq(tool_call_event(
            SESSION, TASK, AGENT, 0, 0, "bash", "1 passed", True, call_id="call-1",
        ), 3),
        _with_seq(agent_call_event(
            SESSION, TASK, AGENT, 0, reasoning="run the tests",
            usage={"input_tokens": 20, "output_tokens": 4},
        ), 4),
        _with_seq(agent_end_event(SESSION, TASK, AGENT, True, "done"), 5),
    ]


def _label(reward=0.8):
    return RewardLabel(
        label_id="label-1",
        session_id=SESSION,
        task_id=TASK,
        reward=reward,
        evaluator="unit-benchmark",
        evaluator_version="v1",
        timestamp=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )


def test_trace_replay_recovers_the_trainable_step_and_outcome():
    facts = _facts()
    facts[3].metadata["execution"] = {
        "world": {"kind": "local", "name": "local", "workspace_root": "/workspace"}
    }
    trajectory = project_trajectory(facts, reward_labels=[_label()])

    assert trajectory.task_description == "Fix it"
    assert trajectory.success is True
    assert trajectory.final_result == "done"
    assert trajectory.reward == 0.8
    (step,) = trajectory.steps
    assert step.messages_sent == [{"role": "user", "content": "Fix it"}]
    assert step.reasoning == "run the tests"
    assert step.actions[0]["args"] == {"cmd": "pytest"}
    assert step.observations[0]["result"] == "1 passed"
    assert step.request_snapshot_id == "sha256:request"
    assert (step.source_trace_seq_start, step.source_trace_seq_end) == (1, 4)


def test_projection_is_deterministic_and_names_its_projector():
    first = project_trajectory(_facts(), reward_labels=[_label()])
    second = project_trajectory(list(reversed(_facts())), reward_labels=[_label()])

    assert first.model_dump() == second.model_dump()
    assert first.metadata["projector_version"] == PROJECTOR_VERSION


def test_code_mode_subcalls_are_observations_not_model_emitted_actions():
    """A program may call tools internally; the provider emitted only the outer call."""
    facts = _facts()
    nested_start = _with_seq(tool_start_event(
        SESSION, TASK, AGENT, 0, 0, "read_file", {"path": "a.py"}, call_id="call-1#1",
    ), 3)
    nested_start.metadata["parent_call_id"] = "call-1"
    nested_result = _with_seq(tool_call_event(
        SESSION, TASK, AGENT, 0, 0, "read_file", "content", True, call_id="call-1#1",
    ), 4)
    nested_result.metadata["parent_call_id"] = "call-1"
    # Make room before the existing assistant/end events while retaining trace order.
    facts[3].seq_no = 5
    facts[4].seq_no = 6
    facts[5].seq_no = 7
    facts = [*facts[:3], nested_start, nested_result, *facts[3:]]

    trajectory = project_trajectory(facts)
    assert [action["name"] for action in trajectory.steps[0].actions] == ["bash"]
    assert [item["name"] for item in trajectory.steps[0].observations] == [
        "read_file", "bash",
    ]


def test_a_trace_with_several_tasks_requires_an_explicit_selection():
    other = _with_seq(agent_start_event(SESSION, "other", AGENT, "Other"), 6)
    with pytest.raises(ValueError, match="task_id is required"):
        project_trajectory([*_facts(), other])


def test_rebuild_is_side_effect_free_until_the_caller_adopts_it(tmp_path):
    """Verification must not replace the live cache merely by comparing with it."""
    manager = TrajectoryManagerServer()
    manager.base_dir = str(tmp_path)

    rebuilt = manager.rebuild_from_trace(
        _facts(), task_id=TASK, reward_labels=[_label()], adopt=False,
    )
    assert rebuilt.task_id == TASK
    assert manager.get(TASK) is None

    adopted = manager.rebuild_from_trace(
        _facts(), task_id=TASK, reward_labels=[_label()], adopt=True,
    )
    assert manager.get(TASK) is adopted
    assert (tmp_path / f"{TASK}.jsonl").exists()


def test_export_prefers_trace_over_a_divergent_live_cache(tmp_path, monkeypatch):
    """The projector is only a source of truth if trainer-facing reads actually use it."""
    manager = TrajectoryManagerServer()
    manager.base_dir = str(tmp_path)
    cached = project_trajectory(_facts())
    cached.steps[0].reasoning = "wrong cache value"
    manager._trajectories[TASK] = cached
    monkeypatch.setattr(
        "agentevolver.trace.server.trace_manager.events", lambda _session: _facts(),
    )

    record = manager.export_sft(TASK)[0]
    assert record["messages"][-1]["content"] == "run the tests"
    assert manager.get(TASK).steps[0].reasoning == "wrong cache value"  # read was non-mutating


def test_reward_updates_append_labels_instead_of_erasing_history(tmp_path):
    manager = TrajectoryManagerServer()
    manager.base_dir = str(tmp_path)
    manager._trajectories[TASK] = project_trajectory(_facts())

    manager.set_reward(TASK, 0.2, evaluator="judge-a", evaluator_version="1")
    manager.set_reward(TASK, 0.9, evaluator="judge-b", evaluator_version="2")

    labels = manager.load_reward_labels(TASK)
    assert [(label.reward, label.evaluator) for label in labels] == [
        (0.2, "judge-a"), (0.9, "judge-b"),
    ]
    rebuilt = manager.rebuild_from_trace(_facts(), task_id=TASK, reward_labels=labels)
    assert rebuilt.reward == 0.9
    assert [step.reward for step in rebuilt.steps] == [0.9]


def test_a_future_reward_label_is_refused_not_replaced_by_an_older_score(
    tmp_path, monkeypatch,
):
    """Ignoring the newest unknown label would silently train on a superseded reward."""
    manager = TrajectoryManagerServer()
    manager.base_dir = str(tmp_path)
    manager._trajectories[TASK] = project_trajectory(_facts())
    manager.set_reward(TASK, 0.2, evaluator="old")
    path = manager._label_path(RewardLabel(
        session_id=SESSION, task_id=TASK, reward=0.9,
    ))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            '{"schema_version":999,"session_id":"session-1",'
            '"task_id":"task-1","reward":0.9}\n'
        )

    with pytest.raises(UnsupportedRewardLabel):
        manager.load_reward_labels(TASK)

    # Trainer-facing exports must not swallow the incompatibility and fall back to the
    # cached 0.2 reward; that would turn a visible upgrade requirement into bad data.
    monkeypatch.setattr(
        "agentevolver.trace.server.trace_manager.events", lambda _session: _facts(),
    )
    with pytest.raises(UnsupportedRewardLabel):
        manager.export_sft(TASK)
