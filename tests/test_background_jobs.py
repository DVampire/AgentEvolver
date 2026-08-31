"""Starting work must not cost a step spent waiting.

Every tool call was foreground. A measured `penguins_analysis` run took eleven minutes,
and its largest single contributor was a `bash_tool` call the agent could only sit
through. That does not merely waste wall-clock: a step is meant to be a decision, and a
step spent waiting is a decision the agent never got to make — indistinguishable, in the
trajectory and in the reward, from one that thought hard and slowly.
"""

import asyncio
import os

import pytest

from agentevolver.job import job_manager
from agentevolver.job.types import Job, JobStatus


class _Ctx:
    id = "test_session"


@pytest.fixture
def workspace(tmp_path):
    from agentevolver.config import config
    from agentevolver.permission import PermissionMode, permission_manager
    previous = getattr(config, "workspace_root", None)
    config.workspace_root = str(tmp_path)
    permission_manager.register(
        "bash_tool", mode=PermissionMode.WORKSPACE_WRITE, workspace=str(tmp_path),
    )
    yield tmp_path
    job_manager.forget(_Ctx.id)
    permission_manager.unregister("bash_tool")
    config.workspace_root = previous


async def _wait_until(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
def test_a_killed_job_is_not_a_failed_one():
    """The agent's own decision must not read as the command's verdict."""
    job = job_manager.register(type="test", label="x", session_id="s1")
    job_manager.kill(job.id)
    assert job_manager.get(job.id).status is JobStatus.KILLED
    job_manager.forget("s1")


def test_the_first_verdict_stands():
    """A process exiting after being killed must not overwrite "killed" with "exited".

    Otherwise a job the agent stopped reads back as one that ran to completion, and the
    agent acts on a result that was never produced.
    """
    job = job_manager.register(type="test", label="x", session_id="s1")
    job_manager.kill(job.id)
    job_manager.finish(job.id, exit_code=0)
    assert job_manager.get(job.id).status is JobStatus.KILLED
    job_manager.forget("s1")


def test_reading_output_does_not_consume_it():
    """An agent that polls has to see earlier output on every read.

    A consuming read makes "nothing new" and "I already took it" the same answer.
    """
    job = job_manager.register(type="test", label="x", session_id="s1")
    job_manager.append_output(job.id, "line-1\n")
    assert "line-1" in job_manager.output(job.id)
    assert "line-1" in job_manager.output(job.id)
    job_manager.forget("s1")


def test_output_is_capped_at_the_head_not_the_tail():
    """A command's closing lines are the ones that say what happened."""
    from agentevolver.job.server import MAX_OUTPUT_CHARS

    job = job_manager.register(type="test", label="x", session_id="s1")
    job_manager.append_output(job.id, "EARLY\n" + "z" * MAX_OUTPUT_CHARS + "\nVERDICT")
    text = job_manager.output(job.id)
    assert text.endswith("VERDICT")
    assert "EARLY" not in text
    assert job_manager.get(job.id).truncated
    job_manager.forget("s1")


def test_a_running_job_is_never_evicted():
    """Forgetting a live job orphans a process nothing can then report on or stop."""
    from agentevolver.job.server import MAX_FINISHED_PER_SESSION

    live = job_manager.register(type="test", label="live", session_id="s2")
    for i in range(MAX_FINISHED_PER_SESSION + 10):
        done = job_manager.register(type="test", label=f"d{i}", session_id="s2")
        job_manager.finish(done.id, exit_code=0)
    assert job_manager.get(live.id) is not None
    job_manager.forget("s2")


def test_jobs_are_scoped_to_their_session():
    """One run must not list, read, or kill another's work."""
    a = job_manager.register(type="test", label="a", session_id="s3")
    b = job_manager.register(type="test", label="b", session_id="s4")
    assert [j.id for j in job_manager.list("s3")] == [a.id]
    assert [j.id for j in job_manager.list("s4")] == [b.id]
    job_manager.forget("s3")
    job_manager.forget("s4")


def test_elapsed_time_separates_working_from_hung():
    """A status alone cannot; it says "running" either way."""
    job = Job(id="j", type="test", label="x")
    assert job.elapsed >= 0
    assert "running" in job.summary()


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_backgrounded_command_returns_at_once_and_is_collected_later(workspace):
    from agentevolver.tool.default.bash import BashTool
    from agentevolver.environment.default.job import JobEnvironment

    started = await BashTool()(
        command="for i in 1 2 3; do echo line-$i; sleep 0.2; done",
        run_in_background=True, ctx=_Ctx())
    assert started.success
    job_id = started.data["job_id"]
    assert job_manager.get(job_id).status is JobStatus.RUNNING, \
        "the call returned only after the command finished; it did not background it"

    output = JobEnvironment()
    assert await _wait_until(lambda: "line-3" in (job_manager.output(job_id) or ""))
    assert await _wait_until(lambda: job_manager.get(job_id).status.is_final)

    collected = await output.output(job_id=job_id, ctx=_Ctx())
    assert "line-1" in collected["message"] and "line-3" in collected["message"]
    assert collected["exit_code"] == 0


@pytest.mark.asyncio
async def test_a_running_job_says_so_rather_than_looking_finished(workspace):
    """Output that simply stops is indistinguishable from a job that ended quietly.

    An agent reading it as finished stops collecting, and never sees the rest.
    """
    from agentevolver.tool.default.bash import BashTool
    from agentevolver.environment.default.job import JobEnvironment

    started = await BashTool()(command="sleep 5", run_in_background=True, ctx=_Ctx())
    result = await JobEnvironment().output(job_id=started.data["job_id"], ctx=_Ctx())
    assert result["running"] is True
    assert "STILL RUNNING" in result["message"]
    job_manager.kill(started.data["job_id"])


@pytest.mark.asyncio
async def test_killing_keeps_what_the_job_already_said(workspace):
    from agentevolver.tool.default.bash import BashTool
    from agentevolver.environment.default.job import JobEnvironment

    started = await BashTool()(
        command="echo before-kill; sleep 30", run_in_background=True, ctx=_Ctx())
    job_id = started.data["job_id"]
    assert await _wait_until(lambda: "before-kill" in (job_manager.output(job_id) or ""))

    await JobEnvironment().kill(job_id=job_id, ctx=_Ctx())
    kept = await JobEnvironment().output(job_id=job_id, ctx=_Ctx())
    assert "before-kill" in kept["message"], "the kill destroyed what had already been learned"


@pytest.mark.asyncio
async def test_killing_a_finished_job_is_not_an_error(workspace):
    """Reporting it as one invites a retry loop against a dead process."""
    from agentevolver.environment.default.job import JobEnvironment

    job = job_manager.register(type="test", label="x", session_id=_Ctx.id)
    job_manager.finish(job.id, exit_code=0)
    result = await JobEnvironment().kill(job_id=job.id, ctx=_Ctx())
    assert result["success"]
    assert "nothing to stop" in result["message"]


@pytest.mark.asyncio
async def test_a_terminal_cannot_be_backgrounded(workspace):
    """Nobody would be there to type at it; the program would draw and wait forever."""
    from agentevolver.tool.default.bash import BashTool

    result = await BashTool()(command="top", tty=True, run_in_background=True, ctx=_Ctx())
    assert not result.success
    assert "run_in_background" in result.message


@pytest.mark.asyncio
async def test_an_unknown_job_id_names_the_ones_that_exist(workspace):
    """A bare "not found" leaves the agent guessing at a handle it already holds."""
    from agentevolver.environment.default.job import JobEnvironment

    job = job_manager.register(type="test", label="x", session_id=_Ctx.id)
    result = await JobEnvironment().output(job_id="job_nope", ctx=_Ctx())
    assert not result["success"]
    assert job.id in result["message"]


# --------------------------------------------------------------------------- #
# Disposal must reach quiescence, not request it
# --------------------------------------------------------------------------- #
def test_a_process_that_ignores_sigterm_is_still_stopped():
    """Sending a signal and returning is a request, not an ending.

    `kill` used to signal the group and mark the job KILLED immediately. A process that
    traps SIGTERM then kept running while the registry reported it dead — the same lie
    the group-signal exists to prevent, arriving through a different door.

    SIGKILL cannot be trapped, so the escalation is what settles it.
    """
    import subprocess
    import sys
    import time

    ignores_term = ("import signal, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    process = subprocess.Popen([sys.executable, "-c", ignores_term], start_new_session=True)
    job = job_manager.register(type="test", label="ignores SIGTERM",
                               session_id="quiescence", handle=process)
    # The handler is installed by the interpreter at startup; signalling before it exists
    # kills the process on the first SIGTERM and tests nothing.
    time.sleep(0.5)

    try:
        job_manager.kill(job.id)
        assert process.poll() is not None, "kill returned while the process was still running"
    finally:
        if process.poll() is None:
            process.kill()
        job_manager.forget("quiescence")


def test_killing_reaps_the_process_rather_than_leaving_a_zombie():
    """A long-lived host accumulates one defunct entry per job otherwise.

    `Popen.wait` is what reaps; signalling alone leaves the exit status uncollected for
    the life of the gateway.
    """
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               start_new_session=True)
    job = job_manager.register(type="test", label="sleeper", session_id="reap",
                               handle=process)
    try:
        job_manager.kill(job.id)
        assert process.returncode is not None, "the exit status was never collected"
    finally:
        job_manager.forget("reap")


def test_killing_a_job_whose_process_is_already_gone_does_not_raise():
    """The common race: the command finished between the listing and the kill."""
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", ""], start_new_session=True)
    process.wait()
    job = job_manager.register(type="test", label="already gone", session_id="gone",
                               handle=process)
    try:
        assert job_manager.kill(job.id) is True
    finally:
        job_manager.forget("gone")


# --------------------------------------------------------------------------- #
# What the agent sees without asking
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unfinished_work_shows_up_in_the_state(workspace):
    """The reason this is an environment rather than three tools.

    Background work is silent by construction: a job that finished, one that failed and
    one that hung look identical from outside — like nothing at all. As tools, "what am I
    still waiting on" was answered only when the agent thought to ask, which is asking it
    to remember the thing it delegated the work in order to forget.
    """
    from agentevolver.environment.default.job import JobEnvironment

    env = JobEnvironment()
    assert (await env.get_state(ctx=_Ctx()))["state"] == "", \
        "with nothing outstanding the state is empty, so the block is omitted entirely"

    job = job_manager.register(type="test", label="a long thing", session_id=_Ctx.id)
    body = (await env.get_state(ctx=_Ctx()))["state"]
    assert job.id in body and "a long thing" in body


@pytest.mark.asyncio
async def test_finished_work_leaves_the_state(workspace):
    """A finished job has said everything it is going to.

    Its output stays readable through `output`, and a line about it every step is prompt
    spent on something that is over — for the whole rest of the run. `list` is where the
    history lives.
    """
    from agentevolver.environment.default.job import JobEnvironment

    env = JobEnvironment()
    job = job_manager.register(type="test", label="done thing", session_id=_Ctx.id)
    assert job.id in (await env.get_state(ctx=_Ctx()))["state"]

    job_manager.finish(job.id, exit_code=0)
    assert (await env.get_state(ctx=_Ctx()))["state"] == "", \
        "a finished job stayed in the state, where it costs prompt every step"
    assert job.id in (await env.list(ctx=_Ctx()))["message"], \
        "`list` is where finished work is still visible"


@pytest.mark.asyncio
async def test_a_long_listing_is_trimmed_and_says_so(workspace):
    """The state is a standing picture, not an archive.

    Silently showing the first twelve would read as "twelve is all there is", which is the
    same failure as an incomplete answer to "what am I waiting on".
    """
    from agentevolver.environment.default.job import JobEnvironment
    from agentevolver.environment.default.job.environment import STATE_JOB_LIMIT

    for index in range(STATE_JOB_LIMIT + 3):
        job_manager.register(type="test", label=f"job {index}", session_id=_Ctx.id)

    body = (await JobEnvironment().get_state(ctx=_Ctx()))["state"]
    assert "and 3 more" in body
    assert "job__list" in body, "the trim must name the way to see the rest"
