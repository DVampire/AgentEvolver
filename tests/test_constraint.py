"""Constraints: per-task budgets, and the urgency the model is told about.

When any limit is hit the task is force-stopped and an unfinished answer is
lost, so two things have to hold. Budgets are per *task* — one run's step count
must never be charged against another's, since these constraint objects are
shared singletons across concurrent runs. And the rendered status has to escalate
in time to be useful: a model told "NORMAL" at 90% consumption gets cut off
mid-thought.
"""

import pytest

from agentevolver.constraint.default.step_constraint import StepConstraint
from agentevolver.constraint.default.token_constraint import TokenConstraint
from agentevolver.constraint.types import (
    Constraint,
    ConstraintContext,
    ConstraintStatus,
    render_status_text,
)


def ctx(task_id="task-1"):
    return ConstraintContext(id=task_id)


# -------------------------------------------------------------------- status
def test_remaining_never_goes_negative():
    """An overshoot must read as 0 left, not as a negative budget."""
    assert ConstraintStatus(name="c", used=150, limit=100).remaining == 0.0


def test_the_ratio_reports_how_much_is_consumed():
    assert ConstraintStatus(name="c", used=25, limit=100).ratio == 0.25


def test_a_zero_limit_does_not_divide_by_zero():
    assert ConstraintStatus(name="c", used=5, limit=0).ratio == 0.0


def test_a_budget_line_reads_in_its_own_unit():
    line = ConstraintStatus(name="tokens", used=1500, limit=10000, unit="tokens").line()
    assert "1,500" in line and "10,000" in line and "8,500 tokens remaining" in line


def test_a_time_budget_reads_in_seconds():
    line = ConstraintStatus(name="wall", used=30.4, limit=120, unit="seconds").line()
    assert line == "wall: 30s / 120s (90s remaining)"


# ------------------------------------------------------------------ urgency
@pytest.mark.parametrize(
    "used, tier",
    [(10, "NORMAL"), (59, "NORMAL"), (60, "TIGHT"), (84, "TIGHT"), (85, "CRITICAL"), (200, "CRITICAL")],
)
def test_urgency_escalates_with_the_most_consumed_budget(used, tier):
    text = render_status_text([ConstraintStatus(name="c", used=used, limit=100)])
    assert f"Status: {tier}" in text


def test_the_worst_budget_drives_the_tier_not_the_average():
    """One nearly-exhausted budget stops the run regardless of the others."""
    text = render_status_text([
        ConstraintStatus(name="steps", used=1, limit=100),
        ConstraintStatus(name="tokens", used=99, limit=100),
    ])
    assert "Status: CRITICAL" in text


def test_a_critical_budget_is_told_to_finish_with_what_it_has():
    text = render_status_text([ConstraintStatus(name="c", used=95, limit=100)])
    assert "Wrap up NOW" in text
    assert "partial result" in text


def test_a_tight_budget_is_told_to_stop_broadening():
    assert "Stop broadening scope" in render_status_text(
        [ConstraintStatus(name="c", used=70, limit=100)]
    )


def test_the_model_is_told_that_hitting_a_limit_loses_the_answer():
    text = render_status_text([ConstraintStatus(name="c", used=1, limit=100)])
    assert "force-stopped" in text
    assert "unfinished answer is lost" in text


def test_every_budget_gets_its_own_line():
    text = render_status_text([
        ConstraintStatus(name="steps", used=1, limit=10, unit="steps"),
        ConstraintStatus(name="tokens", used=2, limit=20, unit="tokens"),
    ])
    assert "- steps:" in text and "- tokens:" in text


def test_plain_dictionaries_are_accepted_as_they_come_off_a_response():
    text = render_status_text([{"name": "c", "used": 90, "limit": 100}])
    assert "Status: CRITICAL" in text


def test_no_budgets_renders_nothing_rather_than_an_empty_header():
    assert render_status_text([]) == ""


def test_the_escalation_thresholds_can_be_retuned():
    statuses = [ConstraintStatus(name="c", used=50, limit=100)]
    assert "NORMAL" in render_status_text(statuses)
    assert "CRITICAL" in render_status_text(statuses, tight_ratio=0.2, critical_ratio=0.4)


# ------------------------------------------------------------ limit resolution
class Probe(Constraint):
    name: str = "probe"


def test_the_default_applies_when_nothing_overrides_it():
    assert Probe()._effective_limit("t1", {}, "max_step", 30) == 30


def test_an_override_is_remembered_for_later_checks():
    """The agent may raise its own budget mid-task; status must stay consistent."""
    probe = Probe()
    assert probe._effective_limit("t1", {"max_step": 50}, "max_step", 30) == 50
    assert probe._effective_limit("t1", {}, "max_step", 30) == 50


def test_a_later_override_replaces_the_earlier_one():
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    assert probe._effective_limit("t1", {"max_step": 10}, "max_step", 30) == 10


def test_an_explicit_none_does_not_count_as_an_override():
    assert Probe()._effective_limit("t1", {"max_step": None}, "max_step", 30) == 30


def test_one_task_s_override_does_not_reach_another():
    """These objects are shared across concurrent runs."""
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    assert probe._effective_limit("t2", {}, "max_step", 30) == 30


def test_cleaning_up_a_task_forgets_its_budget():
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    probe._cleanup("t1")
    assert probe._effective_limit("t1", {}, "max_step", 30) == 30


def test_cleaning_up_an_unknown_task_is_harmless():
    Probe()._cleanup("never-ran")


def test_a_bare_constraint_passes_by_default():
    """A constraint that has not been implemented must not block every run."""
    import asyncio

    assert asyncio.run(Constraint(name="c")({}, ctx())).success is True


# ---------------------------------------------------------------- step budget
@pytest.mark.asyncio
async def test_steps_are_counted_per_check():
    constraint = StepConstraint()
    for expected in (1, 2, 3):
        result = await constraint({}, ctx())
        assert result.data["status"]["used"] == expected
        assert result.success is True


@pytest.mark.asyncio
async def test_exceeding_the_step_cap_fails_the_check():
    constraint = StepConstraint(max_step=2)
    for _ in range(2):
        assert (await constraint({}, ctx())).success is True
    breached = await constraint({}, ctx())
    assert breached.success is False
    assert "Step limit reached" in breached.message


@pytest.mark.asyncio
async def test_two_concurrent_tasks_keep_separate_step_counts():
    constraint = StepConstraint()
    await constraint({}, ctx("a"))
    await constraint({}, ctx("a"))
    assert (await constraint({}, ctx("b"))).data["status"]["used"] == 1


@pytest.mark.asyncio
async def test_the_step_cap_can_be_raised_for_one_task():
    constraint = StepConstraint(max_step=1)
    await constraint({"max_step": 5}, ctx())
    result = await constraint({}, ctx())
    assert result.success is True
    assert result.data["status"]["limit"] == 5


@pytest.mark.asyncio
async def test_the_step_status_is_reported_in_steps():
    result = await StepConstraint()({}, ctx())
    assert result.data["status"]["unit"] == "steps"
    assert result.data["status"]["name"] == "step_constraint"


# --------------------------------------------------------------- declarations
def test_constraints_are_enabled_and_non_evolving_by_default():
    """Evolving a resource limit is how a run talks itself out of its budget."""
    constraint = StepConstraint()
    assert constraint.enabled is True
    assert constraint.enable_evolving is False


def test_the_built_in_budgets_cover_distinct_resources():
    names = {StepConstraint().name, TokenConstraint().name}
    assert len(names) == 2
