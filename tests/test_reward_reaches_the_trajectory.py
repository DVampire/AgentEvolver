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
    return Trajectory(
        session_id=session_id,
        task_id=task_id,
        agent_name="a",
        task_description="t",
        steps=[TrajectoryStep(step_number=0)],
    )


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
        "trajectory it produces will read as reward 0"
    )

    recorder = inspect.getsource(bench._record_reward)
    assert "set_reward" in recorder
    assert "set_reward_by_session" in recorder, (
        "no session fallback: a benchmark that runs one session per task holds the "
        "session id, not the run's task_id, and its rewards would be dropped silently"
    )


def test_recording_a_reward_never_costs_the_benchmark_its_answer():
    """The score is the thing being asked for.

    Failing the run because the footnote could not be written would throw away the answer
    to keep the annotation.
    """
    import agentevolver.benchmark.server as bench

    recorder = inspect.getsource(bench._record_reward)
    assert "try:" in recorder and "logger.warning" in recorder


# --------------------------------------------------------------------------- #
# What was written can be read
# --------------------------------------------------------------------------- #
def test_a_persisted_trajectory_can_be_read_back(trajectories, tmp_path):
    """`_persist` wrote these and nothing read them.

    Every export looks in `self._trajectories`, which holds only what *this* process
    built — so the moment a run ended, the data it produced could no longer be turned
    into the training records this module exists to produce. The JSONL on disk was an
    artifact nothing could open.
    """
    original = _traj("t9", "s9")
    original.steps[0].reasoning = "considered the options"
    original.success = True
    trajectories._trajectories["t9"] = original
    trajectories._persist(original)

    written = tmp_path / "t9.jsonl"
    assert written.exists(), "the fixture's base_dir is not where _persist writes"

    reloaded = trajectories.load(str(written))

    assert reloaded is not None
    assert reloaded.task_id == "t9"
    assert reloaded.session_id == "s9"
    assert reloaded.success is True
    assert [s.reasoning for s in reloaded.steps] == ["considered the options"]


def test_a_reloaded_trajectory_still_exports(trajectories, tmp_path):
    """Reading it back is only worth anything if the records come out the other side."""
    original = _traj("t10", "s10")
    trajectories._trajectories["t10"] = original
    trajectories.set_reward("t10", 0.5)

    reloaded = trajectories.load(str(tmp_path / "t10.jsonl"))

    records = reloaded.to_sft_records()
    assert len(records) == 1
    assert records[0]["reward"] == 0.5, "the reward did not survive the round trip"


def test_a_truncated_last_line_does_not_lose_the_whole_run(trajectories, tmp_path):
    """A killed run leaves a half-written final line — the normal shape of a crash log.

    Failing the file over its last line would discard every step before it, which is the
    part worth having.
    """
    original = _traj("t11", "s11")
    original.steps.append(TrajectoryStep(step_number=1))
    trajectories._trajectories["t11"] = original
    trajectories._persist(original)

    path = tmp_path / "t11.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"step_number": 2, "acti', encoding="utf-8")

    reloaded = trajectories.load(str(path))

    assert reloaded is not None
    assert len(reloaded.steps) == 2, "the intact steps were dropped along with the torn one"


def test_loading_a_directory_skips_what_is_not_a_trajectory(trajectories, tmp_path):
    """A log directory holds more than trajectories; a stray file is not a failure."""
    trajectories._trajectories["t12"] = _traj("t12", "s12")
    trajectories._persist(trajectories._trajectories["t12"])
    (tmp_path / "notes.txt").write_text("not json", encoding="utf-8")

    assert [t.task_id for t in trajectories.load_all(str(tmp_path))] == ["t12"]
