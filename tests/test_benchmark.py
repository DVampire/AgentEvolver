"""Benchmark: slicing a dataset, and the LLM judge that decides a score.

The judge is where a run's number comes from, so its failure modes matter more
than its happy path: a model call that errors, returns nothing, or returns an
unparseable result must fall back to exact matching rather than silently scoring
zero — a benchmark that reports 0% because the grader was down is worse than one
that reports nothing.

Concrete datasets need their files on disk and are not exercised here.
"""

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
    """Replace ``model_manager`` with a scripted stand-in."""

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
    def __init__(self, success=True, parsed_model=None, message=""):
        self.success = success
        self.parsed_model = parsed_model
        self.message = message


# -------------------------------------------------------------------- slicing
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
    assert Scored(start=start, end=end)._apply_slice([1, 2, 3, 4]) == expected


def test_the_end_bound_is_exclusive():
    """``--start 0 --end 1`` must run exactly one task."""
    assert len(Scored(start=0, end=1)._apply_slice(list(range(10)))) == 1


# ------------------------------------------------------------------ metadata
def test_a_benchmark_names_itself_after_its_class():
    class GsmEightK(Benchmark):
        pass

    assert GsmEightK().name == "gsm_eight_k"


def test_a_benchmark_describes_itself_from_its_docstring():
    assert Scored().description == "A benchmark that only exists to be graded."


def test_an_explicit_name_and_description_win():
    bench = Scored(name="custom", description="chosen")
    assert (bench.name, bench.description) == ("custom", "chosen")


def test_the_unimplemented_lifecycle_is_refused_rather_than_silently_passing():
    import asyncio

    bench = Scored()
    for method in (bench.initialize, bench.reset, bench.step, bench.stats):
        with pytest.raises(NotImplementedError):
            asyncio.run(method())


# --------------------------------------------------------------------- judge
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
    """No answer needs no grader — and the call costs money."""
    assert await benchmark.llm_judge(Task(task_id="t", ground_truth="4")) == 0.0
    assert judge.calls == []


@pytest.mark.asyncio
async def test_the_grader_is_shown_the_question_and_both_answers(benchmark, judge):
    judge.result = Reply(parsed_model=JudgeResult(consistent=True))
    await benchmark.llm_judge(Task(task_id="t", input="What is 2+2?", result="four", ground_truth="4"))

    name, payload = judge.calls[0]
    assert name == "judge-model"
    rendered = payload["messages"][1].text
    assert "What is 2+2?" in rendered
    assert "four" in rendered and "4" in rendered
    assert payload["response_format"] is JudgeResult


@pytest.mark.asyncio
async def test_a_grader_that_errors_falls_back_to_exact_matching(benchmark, judge):
    """A benchmark reporting 0% because the grader was down is worse than one
    reporting nothing."""
    judge.result = RuntimeError("model unreachable")
    matching = Task(task_id="t", result="4", ground_truth="4")
    differing = Task(task_id="t", result="5", ground_truth="4")
    assert await benchmark.llm_judge(matching) == 1.0
    assert await benchmark.llm_judge(differing) == 0.0


@pytest.mark.asyncio
async def test_a_failed_grader_response_falls_back_to_exact_matching(benchmark, judge):
    judge.result = Reply(success=False, message="rate limited")
    assert await benchmark.llm_judge(Task(task_id="t", result="4", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_an_unparseable_grader_reply_falls_back_to_exact_matching(benchmark, judge):
    judge.result = Reply(success=True, parsed_model=None)
    assert await benchmark.llm_judge(Task(task_id="t", result="4", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_whitespace_around_an_answer_does_not_change_the_verdict(benchmark, judge):
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result="  4  ", ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_a_non_string_answer_is_graded_on_its_rendering(benchmark, judge):
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result=4, ground_truth="4")) == 1.0


@pytest.mark.asyncio
async def test_a_task_with_no_ground_truth_does_not_grade_as_correct(benchmark, judge):
    judge.result = Reply(success=False)
    assert await benchmark.llm_judge(Task(task_id="t", result="anything")) == 0.0


# ---------------------------------------------------------------------- types
def test_a_task_needs_only_an_id():
    task = Task(task_id="t")
    assert task.score == 0.0
    assert task.result is None
    assert task.ground_truth is None


def test_a_task_carries_benchmark_specific_extras():
    assert Task(task_id="t", extra={"difficulty": "hard"}).extra["difficulty"] == "hard"


def test_fresh_statistics_start_at_zero():
    stats = Stats()
    assert (stats.accuracy, stats.total, stats.correct, stats.wrong) == (0.0, 0, 0, 0)
    assert stats.times == {}


def test_a_judge_result_needs_only_its_verdict():
    assert JudgeResult(consistent=True).reason == ""


def test_a_benchmark_config_round_trips():
    original = BenchmarkConfig(name="gsm8k", description="grade school math")
    assert BenchmarkConfig.model_validate(original.model_dump()).name == "gsm8k"
