"""A terminal send can outlive the turn that typed it.

The foreground path settles on quiet, and quiet is the wrong signal for work that runs for
minutes: a build prints nothing for a stretch and looks finished, so the caller either
burns its whole budget waiting or reads a completion that has not happened.

The registry was already described as kind-agnostic — a background command, a PTY send, a
spawned agent — and covered two of the three. A `job_list_tool` that answers "what is
outstanding" with only some of the outstanding things is worse than not answering: the
parent working out what it is still waiting on reads the gap as nothing.
"""

import asyncio

import pytest

from agentevolver.job import job_manager
from agentevolver.terminal import terminal_manager
from agentevolver.tool.default.terminal import (TerminalOpenTool, TerminalSendTool,
                                                TerminalReadTool)


class _Ctx:
    id = "bg_terminal_session"


@pytest.fixture
def session(tmp_path):
    from agentevolver.config import config
    previous = getattr(config, "workspace_root", None)
    config.workspace_root = str(tmp_path)
    yield
    terminal_manager.forget(_Ctx.id)
    job_manager.forget(_Ctx.id)
    config.workspace_root = previous


async def _wait_until(predicate, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def _open() -> str:
    opened = await TerminalOpenTool()(ctx=_Ctx())
    assert opened.success, opened.message
    return opened.data["terminal_id"]


@pytest.mark.asyncio
async def test_a_backgrounded_send_returns_before_the_command_does(session):
    """The whole point: typing costs a step, waiting does not."""
    terminal_id = await _open()

    started = await TerminalSendTool()(terminal_id=terminal_id, text="sleep 20",
                                       run_in_background=True, ctx=_Ctx())

    assert started.success
    job = job_manager.get(started.data["job_id"])
    assert not job.status.is_final, (
        "the call returned only after the command finished; it did not background it")


@pytest.mark.asyncio
async def test_a_pty_send_lands_in_the_same_registry_as_a_background_command(session):
    """One answer to "what is outstanding", not one per producer."""
    terminal_id = await _open()

    await TerminalSendTool()(terminal_id=terminal_id, text="sleep 20",
                             run_in_background=True, ctx=_Ctx())

    kinds = {j.kind for j in job_manager.list(_Ctx.id)}
    assert "terminal" in kinds, f"a backgrounded send is invisible to job_list; saw {kinds}"


@pytest.mark.asyncio
async def test_the_output_arrives_in_the_job(session):
    """Collected rather than delivered, like every other job."""
    terminal_id = await _open()

    started = await TerminalSendTool()(terminal_id=terminal_id, text="echo bg-marker",
                                       run_in_background=True, ctx=_Ctx())
    job_id = started.data["job_id"]

    assert await _wait_until(lambda: "bg-marker" in (job_manager.output(job_id) or "")), \
        f"the job never collected the output: {job_manager.output(job_id)!r}"
    assert await _wait_until(lambda: job_manager.get(job_id).status.is_final)


@pytest.mark.asyncio
async def test_killing_the_job_stops_watching_and_not_the_command(session):
    """The distinction the result text spells out, because it is easy to read the other way.

    Cancelling the wait is not the same act as stopping the program. Conflating them would
    make "I no longer need to watch this" silently mean "kill it", which is destructive and
    unrecoverable — and the terminal is precisely where a long-running process lives.
    """
    terminal_id = await _open()

    started = await TerminalSendTool()(
        terminal_id=terminal_id,
        # Writes a file after a delay: the file is how we tell whether it kept running.
        text="(sleep 1; echo alive > kept-running.txt)",
        run_in_background=True, ctx=_Ctx())

    from agentevolver.tool.default.job import JobKillTool
    await JobKillTool()(job_id=started.data["job_id"], ctx=_Ctx())

    from agentevolver.config import config
    from pathlib import Path
    marker = Path(config.workspace_root) / "kept-running.txt"
    assert await _wait_until(lambda: marker.exists()), (
        "killing the job also stopped the command; job_kill_tool is supposed to stop "
        "watching, and terminal_signal_tool is what stops the command")


@pytest.mark.asyncio
async def test_the_screen_is_still_readable_while_the_job_watches(session):
    """Two views of one terminal, both live.

    The job collects what the command printed; the screen shows what the terminal looks
    like now. A backgrounded send must not take the screen hostage — the reason to look at
    a terminal is usually that the job's transcript did not answer the question.
    """
    terminal_id = await _open()
    started = await TerminalSendTool()(terminal_id=terminal_id, text="echo on-screen",
                                       run_in_background=True, ctx=_Ctx())

    assert await _wait_until(
        lambda: "on-screen" in (job_manager.output(started.data["job_id"]) or ""))

    read = await TerminalReadTool()(terminal_id=terminal_id, ctx=_Ctx())
    assert read.success
    assert "on-screen" in read.message, "the live screen lost what the job collected"


@pytest.mark.asyncio
async def test_a_background_send_still_checks_permission(session):
    """Backgrounding must not be a way around the check the foreground path makes."""
    terminal_id = await _open()
    tool = TerminalSendTool()

    import inspect
    source = inspect.getsource(tool.__call__)
    permission_at = source.index("permission_manager.check")
    background_at = source.index("run_in_background")
    assert permission_at < source.index("_send_in_background"), (
        "the background branch is taken before the permission check, so a command "
        "refused in the foreground would run when backgrounded")
