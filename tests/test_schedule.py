"""A reminder is a job that has not started, and it must behave like one.

Scheduling was folded into the job registry rather than given its own: a reminder
asks the same three questions as a running command — is it due, what did it say,
cancel it — and a second registry would answer them in a second vocabulary that
drifts from the first. The risk of folding them together is the opposite one: the
registry's existing rules were written for work that is already running, and applied
unchanged they quietly break scheduling. ``is_final`` meant "not running", which
would make every reminder final at birth and hand it straight to the eviction pass
that drops finished jobs.

Nothing here sleeps. The registry reads its clock through one attribute so a test
can pin time and step it; a test that waited for a real interval to prove a reminder
fires would take as long as the interval and flake under load anyway.
"""

import pytest

from agentevolver.environment.default.job import JobEnvironment
from agentevolver.job import job_manager
from agentevolver.job.server import MAX_FINISHED_PER_SESSION
from agentevolver.job.types import JobStatus, ScheduleError
from agentevolver.tool.default.schedule import ScheduleCreateTool

#: A fixed instant to schedule from: 2026-08-15T09:00:00Z. Any constant would do;
#: a real-looking one keeps the absolute-time cases readable.
T0 = 1786784400.0

SESSION = "sched_session"


class _Ctx:
    id = SESSION


class _Clock:
    """A wall clock the test moves by hand."""

    def __init__(self, now: float = T0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    original = job_manager.clock
    fake = _Clock()
    job_manager.clock = fake
    yield fake
    job_manager.clock = original
    job_manager.forget(SESSION)


# --------------------------------------------------------------------------- #
# What may be scheduled
# --------------------------------------------------------------------------- #
def test_exactly_one_selector_is_required(clock):
    """Two selectors would have to be reconciled, and any rule for that is invented.

    None is the more dangerous case: it reads as "schedule it" and would need a
    default delay nobody asked for.
    """
    with pytest.raises(ScheduleError):
        job_manager.schedule(session_id=SESSION, prompt="check the deploy")
    with pytest.raises(ScheduleError):
        job_manager.schedule(
            session_id=SESSION, prompt="check the deploy", after_seconds=60, every_seconds=600
        )


def test_a_delay_is_measured_from_the_registrys_clock(clock):
    """The whole test file depends on this being the same clock due-ness is judged by.

    If scheduling read the wall clock and due-ness read something else, a reminder
    could be permanently one interval away from firing.
    """
    job = job_manager.schedule(session_id=SESSION, prompt="check the deploy", after_seconds=900)
    assert job.due_at == T0 + 900
    assert job_manager.due(SESSION) == []

    clock.advance(900)
    assert [j.id for j in job_manager.due(SESSION)] == [job.id]


def test_an_absolute_time_without_an_offset_names_no_instant(clock):
    """ "09:00" is an instant per time zone, not an instant.

    Guessing the host's zone is how a reminder set from a browser in Shanghai fires
    at breakfast in California — and the guess would look right in every test written
    on a UTC machine.
    """
    with pytest.raises(ScheduleError):
        job_manager.schedule(session_id=SESSION, prompt="hand over", at="2026-08-15T10:00:00")

    job = job_manager.schedule(
        session_id=SESSION, prompt="hand over", at="2026-08-15T18:00:00+08:00"
    )
    assert job.due_at == T0 + 3600


def test_a_time_that_has_already_passed_is_refused_rather_than_fired_at_once(clock):
    """Silently making it due now turns a typo in the year into an immediate interrupt."""
    with pytest.raises(ScheduleError):
        job_manager.schedule(session_id=SESSION, prompt="too late", at="2020-01-01T00:00:00Z")


def test_a_fixed_rate_faster_than_five_minutes_is_refused(clock):
    """Each delivery costs a turn; at thirty seconds the next arrives before the work does.

    That is not a reminder, it is a loop the model cannot get out of.
    """
    with pytest.raises(ScheduleError):
        job_manager.schedule(session_id=SESSION, prompt="poll", every_seconds=30)
    assert job_manager.schedule(session_id=SESSION, prompt="poll", every_seconds=300)


def test_a_reminder_with_nothing_to_say_is_refused(clock):
    """It would come due as a blank interrupt, which the model can only ignore."""
    with pytest.raises(ScheduleError):
        job_manager.schedule(session_id=SESSION, prompt="   ", after_seconds=60)


# --------------------------------------------------------------------------- #
# Coming due
# --------------------------------------------------------------------------- #
def test_a_scheduled_reminder_is_not_a_finished_job(clock):
    """The trap in reusing the registry: ``is_final`` used to mean "not running".

    Read that way a reminder is final the moment it is created, and the eviction pass
    — which drops finished jobs once a session has too many — deletes it before it
    can ever fire. Nothing else in the system would report the loss.
    """
    reminder = job_manager.schedule(session_id=SESSION, prompt="survive", after_seconds=600)
    assert not reminder.status.is_final

    for index in range(MAX_FINISHED_PER_SESSION + 5):
        finished = job_manager.register(type="test", label=f"d{index}", session_id=SESSION)
        job_manager.finish(finished.id, exit_code=0)
    assert job_manager.get(reminder.id) is not None, "the eviction pass ate a pending reminder"


def test_claiming_a_due_reminder_takes_it_exactly_once(clock):
    """Two pollers must not both announce the same occurrence.

    Reading and delivering are separate acts here for exactly this reason: ``due()``
    can be called freely, ``claim_due()`` is the one that consumes.
    """
    job_manager.schedule(session_id=SESSION, prompt="check the deploy", after_seconds=60)
    clock.advance(60)
    assert len(job_manager.claim_due(SESSION)) == 1
    assert job_manager.claim_due(SESSION) == []


def test_a_one_shot_is_over_once_it_has_been_collected(clock):
    """Left scheduled it would fire forever; left with a due time it would look pending."""
    job = job_manager.schedule(session_id=SESSION, prompt="once", after_seconds=60)
    clock.advance(60)
    job_manager.claim_due(SESSION)

    stored = job_manager.get(job.id)
    assert stored.status is JobStatus.EXITED
    assert stored.due_at is None
    assert job_manager.reminders(SESSION) == []


def test_a_repeating_reminder_skips_what_it_missed(clock):
    """An agent busy for an hour must not come back to twelve identical reminders.

    Replaying the backlog is the tempting reading of "every ten minutes" — no
    occurrence was cancelled, after all — and it costs a turn per missed occurrence
    for information that was only ever worth one.
    """
    job = job_manager.schedule(
        session_id=SESSION, prompt="re-read the build log", every_seconds=600
    )
    clock.advance(3600)

    claimed = job_manager.claim_due(SESSION)
    assert len(claimed) == 1, "a repeating reminder delivered its whole backlog"
    assert job_manager.get(job.id).status is JobStatus.SCHEDULED
    assert job_manager.get(job.id).due_at > clock.now


def test_a_repeating_reminder_stays_aligned_to_when_it_was_created(clock):
    """Restarting the interval from "now" makes a reminder set on the hour drift off it.

    Each delivery would push the next one later by however long the claim was late,
    and an hourly reminder ends up wandering across the clock face.
    """
    job = job_manager.schedule(session_id=SESSION, prompt="hourly", every_seconds=3600)
    first_due = job.due_at

    # Claimed 40 seconds late; the next occurrence must still land on the anchor grid.
    clock.advance(3600 + 40)
    job_manager.claim_due(SESSION)
    assert (job_manager.get(job.id).due_at - first_due) % 3600 == 0


def test_a_delivered_reminder_keeps_what_it_said(clock):
    """Same rule as any other job: reading does not consume, and a record keeps its output.

    An agent that sees a reminder, acts, and comes back has to find it still there —
    otherwise "nothing new" and "I already collected it" are the same answer.
    """
    job = job_manager.schedule(session_id=SESSION, prompt="check the ETL", every_seconds=600)
    clock.advance(600)
    job_manager.claim_due(SESSION)

    assert "check the ETL" in job_manager.output(job.id)
    assert "check the ETL" in job_manager.output(job.id)
    assert job_manager.get(job.id).deliveries == 1


def test_the_claim_reports_the_occurrence_it_delivered(clock):
    """The stored record has already advanced, so the caller cannot read it from there.

    Without the occurrence, a delivered reminder can only say "some time ago", and a
    batch of them cannot be put in order.
    """
    job_manager.schedule(session_id=SESSION, prompt="occurrence", every_seconds=600)
    clock.advance(1800)
    claimed = job_manager.claim_due(SESSION)
    assert claimed[0].due_at == T0 + 600, (
        "the snapshot moved with the record instead of pinning the occurrence"
    )


def test_cancelling_a_reminder_stops_it_coming_due(clock):
    """Delete is the same kill that stops a running command — one verb, one registry."""
    job = job_manager.schedule(session_id=SESSION, prompt="never mind", after_seconds=60)
    assert job_manager.kill(job.id)
    clock.advance(600)
    assert job_manager.due(SESSION) == []
    assert job_manager.get(job.id).status is JobStatus.KILLED


def test_reminders_are_scoped_to_their_session(clock):
    """One run's reminders must not fire into another's conversation."""
    mine = job_manager.schedule(session_id=SESSION, prompt="mine", after_seconds=60)
    job_manager.schedule(session_id="other_session", prompt="theirs", after_seconds=60)
    clock.advance(60)
    try:
        assert [j.id for j in job_manager.claim_due(SESSION)] == [mine.id]
    finally:
        job_manager.forget("other_session")


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_tool_says_when_it_will_fire_and_that_it_dies_with_the_run(clock):
    """Both facts are ones a model cannot check for itself and would otherwise assume.

    Left unsaid, "remind me tomorrow" reads as a promise the run cannot keep.
    """
    result = await ScheduleCreateTool()(prompt="check the deploy", after_seconds=900, ctx=_Ctx())
    assert result.success
    assert "15m" in result.message
    assert "session-local" in result.message
    assert result.data["due_at"] == T0 + 900


@pytest.mark.asyncio
async def test_a_refused_schedule_names_what_to_fix(clock):
    """A bare "invalid arguments" sends the model round the same call with a new guess."""
    result = await ScheduleCreateTool()(prompt="poll", every_seconds=30, ctx=_Ctx())
    assert not result.success
    assert "300" in result.message


@pytest.mark.asyncio
async def test_the_listing_says_how_long_until_it_fires_not_how_long_it_has_sat(clock):
    """Elapsed time is the right number for a running command and useless for a reminder.

    "0.0s" against something that has not started is worse than no number: it reads
    as work that just began.
    """
    await ScheduleCreateTool()(prompt="check the deploy", after_seconds=900, ctx=_Ctx())
    listing = await JobEnvironment().list(ctx=_Ctx())
    assert "in 15m" in listing["message"]

    clock.advance(900)
    fired = await JobEnvironment().list(ctx=_Ctx())
    assert "DUE NOW" in fired["message"]


@pytest.mark.asyncio
async def test_cancelling_through_the_tool_does_not_offer_output_that_never_existed(clock):
    """The kill message is written for a command that printed something first.

    Reused verbatim on a reminder it points the agent at an empty record instead of
    confirming the one thing it asked for: this will not fire.
    """
    created = await ScheduleCreateTool()(prompt="never mind", after_seconds=900, ctx=_Ctx())
    result = await JobEnvironment().kill(job_id=created.data["job_id"], ctx=_Ctx())
    assert result["success"]
    assert "will not come due" in result["message"]


@pytest.mark.asyncio
async def test_a_due_reminder_can_be_read_with_the_same_tool_as_any_other_job(clock):
    """The payoff for folding scheduling into the registry: no second way to read it."""
    created = await ScheduleCreateTool()(prompt="check the ETL", after_seconds=60, ctx=_Ctx())
    clock.advance(60)
    job_manager.claim_due(SESSION)
    read = await JobEnvironment().output(job_id=created.data["job_id"], ctx=_Ctx())
    assert "check the ETL" in read["message"]
