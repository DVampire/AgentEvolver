"""A benchmark's number must not depend on whether the grader answered.

The score a run reports comes from `llm_judge`, which asks a model whether the prediction
agrees with the ground truth. That model call has three separate ways of not producing a
verdict — it raises, it comes back unsuccessful, or it comes back successful with nothing
parseable in it — and each arrives on a different branch. All three have to land on exact
matching, because the alternative is a benchmark that reports 0% and looks like a
catastrophic regression when what actually happened is that the grader was rate-limited.

The slicing tests cover the other half of a comparable number: `--start`/`--end` decide
which tasks ran, so an off-by-one there silently changes the denominator between two runs
being compared. Concrete datasets need their files on disk and are not exercised here.
"""

import asyncio

import pytest

from agentevolver.benchmark.types import (
    Benchmark,
    BenchmarkConfig,
    JudgeResult,
    Stats,
    Task,
)


class Scored(Benchmark):
    """A benchmark that only exists to be graded."""

    name: str = "scored"


@pytest.fixture
def benchmark(tmp_path):
    return Scored(base_dir=str(tmp_path / "benchmark"), model_name="judge-model")


@pytest.fixture
def judge(monkeypatch):
    """Replace ``model_manager`` with a scripted stand-in.

    Setting ``result`` to an exception makes the call raise; setting it to a ``Reply``
    makes it return one. ``calls`` records what the judge was asked, which is how a test
    can assert the model was *not* consulted.
    """

    class FakeModel:
        def __init__(self):
            self.result = None
            self.calls = []

        async def __call__(self, name, input):
            self.calls.append((name, input))
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    fake = FakeModel()
    monkeypatch.setattr("agentevolver.model.model_manager", fake)
    return fake


class Reply:
    """The shape `llm_judge` reads back: success, a parsed model, and a message."""

    def __init__(self, success=True, parsed_model=None, message=""):
        self.success = success
        self.parsed_model = parsed_model
        self.message = message


# --------------------------------------------------------------------------- #
# Which tasks a run covers
# --------------------------------------------------------------------------- #
def test_no_bounds_leaves_the_dataset_whole():
    assert Scored()._apply_slice([1, 2, 3]) == [1, 2, 3]


@pytest.mark.parametrize("start, end, expected", [
    (1, None, [2, 3, 4]),
    (None, 2, [1, 2]),
    (1, 3, [2, 3]),
    (0, 0, []),
    (10, 20, []),          # out of range is empty, not an error
    (None, 99, [1, 2, 3, 4]),
])
def test_a_range_selects_the_requested_window(start, end, expected):
    """Each half of the range has to work alone, and neither may raise on a bad bound.

    A start past the end of the dataset is a typo, not an emergency: raising there kills a
    long evaluation over an argument, whereas an empty slice reports plainly that nothing
    was selected.
    """
    assert Scored(start=start, end=end)._apply_slice([1, 2, 3, 4]) == expected


def test_the_end_bound_is_exclusive():
    """``--start 0 --end 1`` must run exactly one task.

    Inclusive is the intuitive reading of "end", and the two interpretations differ by one
    task — enough to move a percentage on a small slice, and never enough to look like a
    bug.
    """
    assert len(Scored(start=0, end=1)._apply_slice(list(range(10)))) == 1


# --------------------------------------------------------------------------- #
# How a benchmark identifies itself
# --------------------------------------------------------------------------- #
def test_a_benchmark_names_itself_after_its_class():
    """The derived name is the registry key and the results directory, not a label.

    `GsmEightK` becoming `gsm_eight_k` is what lets a benchmark be defined by writing one
    class; if the derivation changed, existing results would be written beside the old
    directory rather than into it, and the benchmark would be unreachable under the name
    everything already refers to.
    """
    class GsmEightK(Benchmark):
        pass

    assert GsmEightK().name == "gsm_eight_k"


def test_a_benchmark_describes_itself_from_its_docstring():
    assert Scored().description == "A benchmark that only exists to be graded."


def test_an_explicit_name_and_description_win():
    """Derivation is a default, so it must not overwrite what the author stated."""
    bench = Scored(name="custom", description="chosen")
    assert (bench.name, bench.description) == ("custom", "chosen")


def test_the_unimplemented_lifecycle_is_refused_rather_than_silently_passing():
    """A base class returning None from `step` reads as "the dataset is exhausted".

    Every one of these is meant to be supplied by a subclass. If the base implementations
    returned instead of raising, a benchmark that forgot one would run to completion over
    zero tasks and report a clean result for an evaluation that never happened.
    """
    bench = Scored()
    for method in (bench.initialize, bench.reset, bench.step, bench.stats):
        with pytest.raises(NotImplementedError):
            asyncio.run(method())


# --------------------------------------------------------------------------- #
# What the judge is asked, and what it answers
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_consistent_answer_scores_one(benchmark, judge):
    judge.result = Reply(parsed_model=JudgeResult(consistent=True))
    task = Task(task_id="t", input="2+2?", result="four", ground_truth="4")
    assert await benchmark.llm_judge(task) == 1.0


@pytest.mark.asyncio
async def test_an_inconsistent_answer_scores_zero(benchmark, judge):
    judge.result = Reply(parsed_model=JudgeResult(consistent=False))
    task = Task(task_id="t", input="2+2?", result="five", ground_truth="4")
    assert await benchmark.llm_judge(task) == 0.0


@pytest.mark.asyncio
async def test_an_empty_prediction_scores_zero_without_asking_the_model(benchmark, judge):
    """No answer needs no grader — and the call costs money.

    On a run where the agent failed early, every task arrives with an empty prediction; a
    judge consulted anyway would bill a model call per task to be told what was already
    known from the empty string.
    """
    assert await benchmark.llm_judge(Task(task_id="t", ground_truth="4")) == 0.0
    assert judge.calls == []


@pytest.mark.asyncio
async def test_the_grader_is_shown_the_question_and_both_answers(benchmark, judge):
    """A grader given only the two answers cannot grade anything but string similarity.

    "four" and "4" agree because the question was arithmetic; without the question in the
    prompt the judge has no basis for that call, and the structured `response_format` is
    what keeps its reply a parseable verdict instead of prose that has to be re-read by a
    regex.
    """
    judge.result = Reply(parsed_model=JudgeResult(consistent=True))
    await benchmark.llm_judge(Task(task_id="t", input="What is 2+2?", result="four", ground_truth="4"))

    name, payload = judge.calls[0]
    assert name == "judge-model"
    # messages[0] is the grading instruction; messages[1] carries the case.
    rendered = payload["messages"][1].text
    assert "What is 2+2?" in rendered
    assert "four" in rendered and "4" in rendered
    assert payload["response_format"] is JudgeResult


# --------------------------------------------------------------------------- #
# When no verdict comes back
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_grader_that_errors_falls_back_to_exact_matching(benchmark, judge):
    """A benchmark reporting 0% because the grader was down is worse than one
    reporting nothing.

    Both directions are asserted, because a fallback that scored everything 1.0 would be
    the same defect wearing a friendlier number.
    """
    judge.result = RuntimeError("model unreachable")
    matching = Task(task_id="t", result="4", ground_truth="4")
    differing = Task(task_id="t", result="5", ground_truth="4")
    assert await benchmark.llm_judge(matching) == 1.0
    assert await benchmark.llm_judge(differing) == 0.0


@pytest.mark.asyncio
async def test_a_failed_grader_response_falls_back_to_exact_matching(benchmark, judge):
    """Rate limiting arrives as a returned failure, not an exception — a different branch.

    Nothing raises here, so a fallback written only inside `except` would never run, and
    the unsuccessful reply would be read as "not consistent".
    """
    judge.result = Reply(success=False, message="rate limited")
    assert await benchmark.llm_judge(Task(task_id="t", result="4", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_an_unparseable_grader_reply_falls_back_to_exact_matching(benchmark, judge):
    """The third shape: the call succeeded and there is still no verdict in it.

    `success=True` with `parsed_model=None` is what a truncated or malformed structured
    output looks like. It is the easiest of the three to read as a negative verdict,
    because the response itself claims to have worked.
    """
    judge.result = Reply(success=True, parsed_model=None)
    assert await benchmark.llm_judge(Task(task_id="t", result="4", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_whitespace_around_an_answer_does_not_change_the_verdict(benchmark, judge):
    """Answers are extracted from model output, so they arrive padded more often than not."""
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result="  4  ", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_a_non_string_answer_is_graded_on_its_rendering(benchmark, judge):
    """A tool that returns a number gives `result` the int 4 against the truth "4".

    Compared without rendering, those are simply unequal, and every task answered by a
    calculator-style tool scores zero on the fallback path.
    """
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result=4, ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_a_task_with_no_ground_truth_does_not_grade_as_correct(benchmark, judge):
    """Nothing to disagree with is not agreement.

    The truth renders as the empty string, and a fallback that stopped at "no mismatch
    found" would hand full marks to every task whose ground truth is missing — turning a
    dataset loading bug into a perfect score.
    """
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result="anything")) == 0.0


# --------------------------------------------------------------------------- #
# Defaults on the records a benchmark passes around
# --------------------------------------------------------------------------- #
def test_a_task_needs_only_an_id():
    """A task is created before it has been answered, so every result field starts empty."""
    task = Task(task_id="t")
    assert task.score == 0.0
    assert task.result is None
    assert task.ground_truth is None


def test_a_task_carries_benchmark_specific_extras():
    assert Task(task_id="t", extra={"difficulty": "hard"}).extra["difficulty"] == "hard"


def test_fresh_statistics_start_at_zero():
    """Accuracy before any task has run must be 0.0 and not an error or a None."""
    stats = Stats()
    assert (stats.accuracy, stats.total, stats.correct, stats.wrong) == (0.0, 0, 0, 0)
    assert stats.times == {}


def test_a_judge_result_needs_only_its_verdict():
    """The reason is optional: a model that omits it must not fail to parse."""
    assert JudgeResult(consistent=True).reason == ""


def test_a_benchmark_config_round_trips():
    """Registration serializes these, so dump-then-validate has to return the same thing."""
    original = BenchmarkConfig(name="gsm8k", description="grade school math")
    assert BenchmarkConfig.model_validate(original.model_dump()).name == "gsm8k"
