"""A scored run must carry its score, or the corpus says every run was worth nothing.

A trajectory exists to be trained on, and `reward` is the training signal. Nothing called
`set_reward`: the benchmark computed a score per task, averaged the scores, and dropped
them. Every trajectory this framework had ever written carried `reward = 0.0` — 61 of 61
in a real output tree, successes and failures alike, indistinguishable.

Both halves were built for each other and never joined. `set_reward` keys on the run's
`task_id`, which `Response.data` already carries, and `set_reward_by_session`'s own
docstring names the benchmark case it was written for.
"""

import inspect

import pytest

from agentevolver.trajectory import trajectory_manager
from agentevolver.trajectory.types import Trajectory, TrajectoryStep


@pytest.fixture
def trajectories(tmp_path):
    trajectory_manager.base_dir = str(tmp_path)
    held = dict(trajectory_manager._trajectories)
    trajectory_manager._trajectories.clear()
    yield trajectory_manager
    trajectory_manager._trajectories.clear()
    trajectory_manager._trajectories.update(held)


def _traj(task_id: str, session_id: str) -> Trajectory:
    return Trajectory(session_id=session_id, task_id=task_id, agent_name="a",
                      task_description="t", steps=[TrajectoryStep(step_number=0)])


# --------------------------------------------------------------------------- #
# The score reaches the run
# --------------------------------------------------------------------------- #
def test_a_reward_reaches_every_step_of_the_run(trajectories):
    """Steps are what a trainer reads; a reward only on the header trains nothing."""
    trajectories._trajectories["t1"] = _traj("t1", "s1")

    trajectories.set_reward("t1", 0.75)

    assert [s.reward for s in trajectories._trajectories["t1"].steps] == [0.75]


def test_set_reward_says_whether_it_found_anything(trajectories):
    """A caller holding a session id cannot otherwise tell recorded from dropped.

    It returned `None` either way, so the benchmark had no way to know its fallback was
    needed — which is how a reward goes nowhere and nothing says so.
    """
    trajectories._trajectories["t1"] = _traj("t1", "s1")

    assert trajectories.set_reward("t1", 1.0) == "t1"
    assert trajectories.set_reward("no-such-task", 1.0) is None


def test_a_benchmark_holding_only_a_session_id_still_records(trajectories):
    """One agent session per task is the shape the benchmark actually runs."""
    trajectories._trajectories["t1"] = _traj("t1", "s1")

    assert trajectories.set_reward_by_session("s1", 0.5) == 1
    assert trajectories._trajectories["t1"].steps[0].reward == 0.5


# --------------------------------------------------------------------------- #
# The benchmark is the caller
# --------------------------------------------------------------------------- #
def test_the_benchmark_records_the_score_it_just_computed():
    """The join that did not exist. Read from source: `_safe_eval` is a closure over a
    live benchmark and a semaphore, and the fact under test is that the call is there at
    all — the same question `test_run_lifecycle_wiring.py` asks of the run loop.
    """
    import agentevolver.benchmark.server as bench

    source = inspect.getsource(bench)
    assert "_record_reward" in source, (
        "the benchmark computes a per-task score and no longer records it; every "
        "trajectory it produces will read as reward 0")

    recorder = inspect.getsource(bench._record_reward)
    assert "set_reward" in recorder
    assert "set_reward_by_session" in recorder, (
        "no session fallback: a benchmark that runs one session per task holds the "
        "session id, not the run's task_id, and its rewards would be dropped silently")


def test_recording_a_reward_never_costs_the_benchmark_its_answer():
    """The score is the thing being asked for.

    Failing the run because the footnote could not be written would throw away the answer
    to keep the annotation.
    """
    import agentevolver.benchmark.server as bench

    recorder = inspect.getsource(bench._record_reward)
    assert "try:" in recorder and "logger.warning" in recorder
