"""Three capabilities were built with no call site, and each looked finished.

`job_manager.forget`, `terminal_manager.forget` and `job_manager.claim_due` all existed,
were tested, and were never called by anything. A registry with a reaper nobody invokes
leaks; a reminder nobody delivers is one the agent must remember to go and look for, which
is the task it set the reminder to avoid. Plan mode had the same shape from the other side
— the gate worked, but nothing told the model the mode was on, so it learned from being
refused.

None of that shows up in a unit test of the piece itself. What catches it is asking
whether the run loop reaches the piece at all, which is what this file does.
"""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from agentevolver.agent.types import Agent

ROOT = Path(__file__).resolve().parents[1]


def _source_of(method_name: str) -> str:
    return inspect.getsource(getattr(Agent, method_name))


def _calls_in(method_name: str) -> set:
    """Every attribute or plain call made inside one method."""
    # dedent, not cleandoc: a method's source arrives indented, and cleandoc strips the
    # leading whitespace only from the first line, leaving a body `ast.parse` rejects.
    # The first draft used cleandoc and reported three call sites as missing that were
    # plainly there — loud, but pointing at the wrong file.
    tree = ast.parse(textwrap.dedent(_source_of(method_name)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


# --------------------------------------------------------------------------- #
# The reapers are reached
# --------------------------------------------------------------------------- #
def test_a_finished_run_releases_its_jobs_and_terminals():
    """`atexit` was the only reaper, and in a long-lived gateway it never fires.

    Both registries are session-scoped and both hold real OS resources — a backgrounded
    process group and a PTY with a shell on it. Without this call every finished session
    left them behind until the host restarted.
    """
    assert "_release_session_resources" in _calls_in("_conclude"), (
        "_conclude no longer releases the run's resources; jobs and terminals will "
        "survive the session that created them")

    released = _calls_in("_release_session_resources") | _calls_in("_forget_jobs") \
        | _calls_in("_forget_terminals")
    assert "forget" in released, "nothing in the release path actually calls forget()"


def test_releasing_resources_cannot_fail_a_finished_run():
    """The run is over by the time this happens.

    Raising here would turn a completed task into a failed one over cleanup — the work is
    done and the caller would never learn it. The registry says so in the log instead.
    """
    assert "try:" in _source_of("_release_session_resources")
    assert "logger.warning" in _source_of("_release_session_resources")


# --------------------------------------------------------------------------- #
# What rides beside the batch
# --------------------------------------------------------------------------- #
def test_a_due_reminder_is_pushed_rather_than_waited_for():
    """`job_list_tool` could show a due reminder; nothing made the agent look.

    A reminder the agent has to remember to check is not a reminder.
    """
    assert "_deliver_due_reminders" in _calls_in("_prepare_round")
    assert "claim_due" in _calls_in("_deliver_due_reminders"), (
        "the delivery path does not claim, so a reminder would either never arrive or "
        "arrive on every step")


def test_a_reminder_is_claimed_so_it_arrives_once():
    """Announcing twice is worse than not announcing.

    The model reads the repetition as a new event and acts on the same thing again.
    """
    from agentevolver.job import job_manager

    job_manager.clock = lambda: 1_000.0
    job = job_manager.schedule(session_id="wiring", prompt="check the build",
                               after_seconds=10)
    job_manager.clock = lambda: 1_100.0          # past due

    assert [j.id for j in job_manager.claim_due("wiring")] == [job.id]
    assert job_manager.claim_due("wiring") == [], "the same reminder came due twice"

    job_manager.forget("wiring")
    job_manager.clock = __import__("time").time


def test_plan_mode_says_it_is_on_instead_of_waiting_to_refuse():
    """The model otherwise infers a mode nobody mentioned from a single error."""
    assert "_announce_plan_mode" in _calls_in("_prepare_round")
    assert "PLAN_MODE_NOTICE" in _source_of("_announce_plan_mode")


def test_the_plan_notice_stays_out_of_the_cached_prefix():
    """It is a mode that toggles mid-session.

    Text in the system message sits ahead of the cache breakpoint, so switching plan mode
    on would rewrite the prefix and discard the session's cached tokens to deliver one
    paragraph. It rides in the volatile section instead.
    """
    source = _source_of("_announce_plan_mode")
    assert "action_errors" in source, (
        "the notice no longer rides in the volatile section; if it moved into the system "
        "prompt, toggling plan mode now costs the whole cached prefix")
