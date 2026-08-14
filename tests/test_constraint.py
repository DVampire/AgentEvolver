"""Whose budget is being spent, and how soon the model is told it is running out.

Hitting a limit force-stops the task: whatever the agent had half-written is discarded.
That makes two things load-bearing. Budgets are per *task*, and the constraint objects
are shared singletons across concurrent runs — a counter kept on the instance instead of
under the task id charges one run's steps to another, so a short task dies because a long
one was busy, and the two runs are no longer comparable.

The other is the status block. It is the only warning the model gets, so the tier has to
escalate while there is still budget left to act on: a model told "NORMAL" at 90%
consumption plans another round of exploration and is cut off mid-thought. The tier
follows the *worst* budget, because the worst one is what actually stops the run.
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


# --------------------------------------------------------------------------- #
# Reading a single budget
# --------------------------------------------------------------------------- #
def test_remaining_never_goes_negative():
    """An overshoot must read as 0 left, not as a negative budget.

    A check can be called once past the limit before the run winds down, so the
    overshoot is a normal state rather than an impossible one. "-50 remaining" in the
    prompt is a number the model has no way to interpret.
    """
    assert ConstraintStatus(name="c", used=150, limit=100).remaining == 0.0


def test_the_ratio_reports_how_much_is_consumed():
    assert ConstraintStatus(name="c", used=25, limit=100).ratio == 0.25


def test_a_zero_limit_does_not_divide_by_zero():
    """Limits come from config and from per-call overrides, so zero is reachable — and
    the ratio is computed on every check, for every constraint, in the hot loop."""
    assert ConstraintStatus(name="c", used=5, limit=0).ratio == 0.0


def test_a_budget_line_reads_in_its_own_unit():
    """Token counts are read by a model, so thousands separators are not decoration:
    ``8500`` and ``85000`` are easy to confuse at a glance and lead to opposite plans."""
    line = ConstraintStatus(name="tokens", used=1500, limit=10000, unit="tokens").line()
    assert "1,500" in line and "10,000" in line and "8,500 tokens remaining" in line


def test_a_time_budget_reads_in_seconds():
    """Seconds get their own format, whole and suffixed. The used value here is
    deliberately fractional — a raw float would render "30.4" and invite the model to
    reason about precision that the wall clock does not have.
    """
    line = ConstraintStatus(name="wall", used=30.4, limit=120, unit="seconds").line()
    assert line == "wall: 30s / 120s (90s remaining)"


# --------------------------------------------------------------------------- #
# The urgency the model is shown
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "used, tier",
    [(10, "NORMAL"), (59, "NORMAL"), (60, "TIGHT"), (84, "TIGHT"), (85, "CRITICAL"), (200, "CRITICAL")],
)
def test_urgency_escalates_with_the_most_consumed_budget(used, tier):
    """The values sit either side of the 0.6 and 0.85 thresholds, one step apart.

    Off-by-one at a boundary is invisible in normal use and costs a run: an agent that
    reaches CRITICAL one check later than it should is one check short of wrapping up.
    """
    text = render_status_text([ConstraintStatus(name="c", used=used, limit=100)])
    assert f"Status: {tier}" in text


def test_the_worst_budget_drives_the_tier_not_the_average():
    """One nearly-exhausted budget stops the run regardless of the others.

    Averaging is the tempting reading and here it would report NORMAL — 50% across the
    two — for a task that is one token check away from being killed.
    """
    text = render_status_text([
        ConstraintStatus(name="steps", used=1, limit=100),
        ConstraintStatus(name="tokens", used=99, limit=100),
    ])
    assert "Status: CRITICAL" in text


def test_a_critical_budget_is_told_to_finish_with_what_it_has():
    """The tier alone is a number; the instruction is what changes behaviour.

    Naming the partial result matters — a model that knows an unfinished answer is lost
    consolidates, where one that only sees "CRITICAL" often starts a new subtask.
    """
    text = render_status_text([ConstraintStatus(name="c", used=95, limit=100)])
    assert "Wrap up NOW" in text
    assert "partial result" in text


def test_a_tight_budget_is_told_to_stop_broadening():
    """TIGHT is the tier with something to do: narrow scope while there is still budget
    to finish the narrowed version."""
    assert "Stop broadening scope" in render_status_text(
        [ConstraintStatus(name="c", used=70, limit=100)]
    )


def test_the_model_is_told_that_hitting_a_limit_loses_the_answer():
    """Stated at NORMAL, before any budget is tight.

    Without the consequence spelled out, remaining budget reads as information rather
    than a deadline, and the model spends it as if running out were merely the end.
    """
    text = render_status_text([ConstraintStatus(name="c", used=1, limit=100)])
    assert "force-stopped" in text
    assert "unfinished answer is lost" in text


def test_every_budget_gets_its_own_line():
    """A tier without the underlying numbers gives the model nothing to plan against —
    "TIGHT" says to hurry, "3 of 10 steps" says how much room is left."""
    text = render_status_text([
        ConstraintStatus(name="steps", used=1, limit=10, unit="steps"),
        ConstraintStatus(name="tokens", used=2, limit=20, unit="tokens"),
    ])
    assert "- steps:" in text and "- tokens:" in text


def test_plain_dictionaries_are_accepted_as_they_come_off_a_response():
    """Statuses reach the renderer as ``Response.data["status"]``, which is a dump.

    Requiring the model class would mean every caller re-validating first; missing that
    conversion in one place silently drops that constraint from the prompt.
    """
    text = render_status_text([{"name": "c", "used": 90, "limit": 100}])
    assert "Status: CRITICAL" in text


def test_no_budgets_renders_nothing_rather_than_an_empty_header():
    """An agent with no constraints configured would otherwise get a resource-limit
    warning listing no limits — which is worse than saying nothing at all."""
    assert render_status_text([]) == ""


def test_the_escalation_thresholds_can_be_retuned():
    """The same 50%-consumed status reads NORMAL by default and CRITICAL once the
    thresholds move, so an agent on a tight external deadline can be made to conclude
    earlier without rewriting the renderer."""
    statuses = [ConstraintStatus(name="c", used=50, limit=100)]
    assert "NORMAL" in render_status_text(statuses)
    assert "CRITICAL" in render_status_text(statuses, tight_ratio=0.2, critical_ratio=0.4)


# --------------------------------------------------------------------------- #
# Which limit applies, for which task
# --------------------------------------------------------------------------- #
class Probe(Constraint):
    """A constraint with no logic of its own, used to exercise limit bookkeeping."""

    name: str = "probe"


def test_the_default_applies_when_nothing_overrides_it():
    assert Probe()._effective_limit("t1", {}, "max_step", 30) == 30


def test_an_override_is_remembered_for_later_checks():
    """The agent may raise its own budget mid-task; status must stay consistent.

    The override arrives on one call and every later check passes an empty input. If it
    were not remembered, the limit would snap back to the default and the status line
    would alternate between two numbers on consecutive steps.
    """
    probe = Probe()
    assert probe._effective_limit("t1", {"max_step": 50}, "max_step", 30) == 50
    assert probe._effective_limit("t1", {}, "max_step", 30) == 50


def test_a_later_override_replaces_the_earlier_one():
    """Remembering must not mean latching: lowering a budget mid-task has to take effect,
    or a raise can never be walked back."""
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    assert probe._effective_limit("t1", {"max_step": 10}, "max_step", 30) == 10


def test_an_explicit_none_does_not_count_as_an_override():
    """Callers build the input dict from optional arguments, so an unset limit arrives as
    a present key holding ``None``. Recording it would set the effective limit to nothing
    at all."""
    assert Probe()._effective_limit("t1", {"max_step": None}, "max_step", 30) == 30


def test_one_task_s_override_does_not_reach_another():
    """These objects are shared across concurrent runs.

    A limit kept on the instance rather than under the task id lets one run raise the cap
    for everybody — the budgets stop meaning anything, and nothing reports that they have.
    """
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    assert probe._effective_limit("t2", {}, "max_step", 30) == 30


def test_cleaning_up_a_task_forgets_its_budget():
    """The state dict lives as long as the process. Without removal it grows with every
    task, and a reused task id inherits the previous run's raised limit."""
    probe = Probe()
    probe._effective_limit("t1", {"max_step": 50}, "max_step", 30)
    probe._cleanup("t1")
    assert probe._effective_limit("t1", {}, "max_step", 30) == 30


def test_cleaning_up_an_unknown_task_is_harmless():
    """Teardown runs on paths where no check ever fired, so the id is often absent."""
    Probe()._cleanup("never-ran")


def test_a_bare_constraint_passes_by_default():
    """A constraint that has not been implemented must not block every run.

    Subclasses that forget ``__call__`` — including ones an evolving system writes — fall
    through to this, so the safe default is permissive; a default failure would stop every
    task at its first check.
    """
    import asyncio

    assert asyncio.run(Constraint(name="c")({}, ctx())).success is True


# --------------------------------------------------------------------------- #
# The step budget end to end
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_steps_are_counted_per_check():
    """One check is one step: the constraint is called once per think-and-act round and
    has no other signal to count from."""
    constraint = StepConstraint()
    for expected in (1, 2, 3):
        result = await constraint({}, ctx())
        assert result.data["status"]["used"] == expected
        assert result.success is True


@pytest.mark.asyncio
async def test_exceeding_the_step_cap_fails_the_check():
    """The cap is inclusive — two steps under a cap of two both pass, and the third is
    refused. Failing on the second would silently cost every run one step."""
    constraint = StepConstraint(max_step=2)
    for _ in range(2):
        assert (await constraint({}, ctx())).success is True
    breached = await constraint({}, ctx())
    assert breached.success is False
    assert "Step limit reached" in breached.message


@pytest.mark.asyncio
async def test_two_concurrent_tasks_keep_separate_step_counts():
    """The counter lives on a shared instance, so this is the failure the whole per-task
    state exists to prevent: task ``b``'s first check reads 1, not 3."""
    constraint = StepConstraint()
    await constraint({}, ctx("a"))
    await constraint({}, ctx("a"))
    assert (await constraint({}, ctx("b"))).data["status"]["used"] == 1


@pytest.mark.asyncio
async def test_the_step_cap_can_be_raised_for_one_task():
    """Raising the cap has to move the reported limit too, not just stop the refusal —
    the status line is what the model plans the rest of the task against."""
    constraint = StepConstraint(max_step=1)
    await constraint({"max_step": 5}, ctx())
    result = await constraint({}, ctx())
    assert result.success is True
    assert result.data["status"]["limit"] == 5


@pytest.mark.asyncio
async def test_the_step_status_is_reported_in_steps():
    """The unit picks the rendering, and the name is how one budget's line is told apart
    from another's when several are shown together."""
    result = await StepConstraint()({}, ctx())
    assert result.data["status"]["unit"] == "steps"
    assert result.data["status"]["name"] == "step_constraint"


# --------------------------------------------------------------------------- #
# What a constraint declares about itself
# --------------------------------------------------------------------------- #
def test_constraints_are_enabled_and_non_evolving_by_default():
    """Evolving a resource limit is how a run talks itself out of its budget.

    Both defaults point the same way: a constraint is on unless someone turns it off, and
    is not something the self-improvement loop may rewrite.
    """
    constraint = StepConstraint()
    assert constraint.enabled is True
    assert constraint.enable_evolving is False


def test_the_built_in_budgets_cover_distinct_resources():
    """Names are the key constraints are registered and reported under, so two sharing
    one would collapse into a single line and a single budget."""
    names = {StepConstraint().name, TokenConstraint().name}
    assert len(names) == 2
