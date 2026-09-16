"""A terminal must outlive the call that typed into it, and must not outlive its session.

Every `bash_tool` call is a fresh process, so `cd`, an exported variable, an activated
environment and an ssh hop are all undone the moment the call returns; anything with a
prompt cannot be reached at all. These tests hold the two halves of the fix together.
The first half is persistence: state set by one call is still there for the next. The
second is reaping, which is the half that fails silently — a pty nobody holds is a live
shell with no id, no listing and no way to stop it, and the run it belongs to ends without
ever noticing.

Real ptys throughout, with short waits. A mocked terminal would agree with whatever the
implementation believes about job control, foreground process groups and hang-up, which
is exactly where this went wrong before.
"""

import asyncio
import os

import pytest

from agentevolver.terminal import TerminalBusy, TerminalStatus, WaitReason, terminal_manager
from agentevolver.terminal.server import MAX_LIVE_PER_SESSION

SESSION = "test_terminal_session"

#: Long enough for bash to echo a line and answer it, short enough that a hung test costs
#: seconds rather than a run.
SEND_TIMEOUT = 6.0


class _Ctx:
    id = SESSION


@pytest.fixture
def session():
    """Close every terminal the test opened, whether it passed or not.

    Without this a failing test leaks a shell that runs for the rest of the pytest
    process — and, if it started anything, for longer than that.
    """
    yield SESSION
    terminal_manager.forget(SESSION)


def _open(**kwargs):
    return terminal_manager.open(session_id=SESSION, **kwargs)


def test_failed_terminal_close_retains_retry_handle(monkeypatch):
    from types import SimpleNamespace

    def fail():
        raise RuntimeError("close failed")
    terminal = SimpleNamespace(id="retry-terminal", session_id="retry-session", started_at=0,
                               elapsed=0, status=TerminalStatus.RUNNING, close=fail)
    terminal_manager._terminals[terminal.id] = terminal
    try:
        with pytest.raises(RuntimeError, match="awaiting cleanup"):
            terminal_manager.forget("retry-session")
        assert terminal_manager.get(terminal.id) is terminal
        terminal.status = TerminalStatus.EXITED
        terminal.close = lambda: True
        terminal_manager.forget("retry-session")
        assert terminal_manager.get(terminal.id) is None
    finally:
        terminal_manager._terminals.pop(terminal.id, None)


def test_terminal_eof_does_not_mean_process_exit():
    import subprocess
    import threading
    from types import SimpleNamespace
    from agentevolver.terminal.types import Terminal

    def still_running(**kwargs):
        raise subprocess.TimeoutExpired("shell", 5)

    terminal = SimpleNamespace(
        _process=SimpleNamespace(poll=lambda: None, wait=still_running),
        _lock=threading.Lock(), status=TerminalStatus.RUNNING,
        exit_code=None, ended_at=None,
    )
    Terminal._mark_exited(terminal)
    assert terminal.status is TerminalStatus.RUNNING
    assert terminal.ended_at is None
    terminal._process.poll = lambda: 0
    Terminal._mark_exited(terminal)
    assert terminal.status is TerminalStatus.EXITED
    assert terminal.exit_code == 0


async def _alive(pid: int, deadline: float = 3.0) -> bool:
    """Whether `pid` is still there, giving it up to `deadline` seconds to die."""
    for _ in range(int(deadline / 0.1)):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        await asyncio.sleep(0.1)
    return True


# --------------------------------------------------------------------------- #
# What persistence is for
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_directory_change_survives_the_call_that_made_it(session, tmp_path):
    """The whole point: `cd` in one call, and the next call is still there.

    Under `bash_tool` the second call is a new process starting from the workspace root,
    so a `cd` reads as having worked while changing nothing the agent can use.
    """
    terminal = _open(name="cwd")
    await terminal.send(f"cd {tmp_path}", timeout=SEND_TIMEOUT)
    output, _ = await terminal.send("pwd", timeout=SEND_TIMEOUT)
    assert str(tmp_path) in output


@pytest.mark.asyncio
async def test_an_exported_variable_survives_too(session):
    """Environment state is the venv case in miniature.

    `source .venv/bin/activate` is exactly this — a variable set in one call that every
    later call depends on — and it is the state agents most often lose without noticing,
    because the next command still runs, just against the wrong interpreter.
    """
    terminal = _open()
    await terminal.send("export MARKER=orange", timeout=SEND_TIMEOUT)
    output, _ = await terminal.send('echo "colour=$MARKER"', timeout=SEND_TIMEOUT)
    assert "colour=orange" in output


@pytest.mark.asyncio
async def test_a_repl_still_holds_what_it_built_on_the_next_call(session):
    """An interpreter is the case a one-shot process cannot express at all.

    `bash_tool` can run `python3 -c`, but it cannot be *at* a prompt: the process it
    started is killed when the call returns, so anything the session built — a loaded
    dataframe, a connection, a model — is gone before the next line can use it.
    """
    terminal = _open(command="python3 -i")
    await terminal.send("value = 6 * 7", timeout=SEND_TIMEOUT)
    output, _ = await terminal.send("print('answer', value)", timeout=SEND_TIMEOUT)
    assert "answer 42" in output


@pytest.mark.asyncio
async def test_a_send_returns_only_what_it_caused(session):
    """A send that returns the whole screen makes the last command's output look like this one's.

    The previous result is still sitting on screen. An agent handed it again has no way to
    tell which command produced it, and will happily read a stale success as a new one.
    """
    terminal = _open()
    await terminal.send("echo first-command-output", timeout=SEND_TIMEOUT)
    output, _ = await terminal.send("echo second-command-output", timeout=SEND_TIMEOUT)
    assert "second-command-output" in output
    assert "first-command-output" not in output


@pytest.mark.asyncio
async def test_the_screen_is_what_the_terminal_displays_not_the_bytes_that_drew_it(session):
    """A cleared screen stays cleared, and escape sequences never reach the agent.

    Two wrong answers are available here. One is handing over the raw stream, where a few
    hundred visible characters arrive as kilobytes of control codes. The other is the rule
    `render_terminal` uses for one-shot commands — keep the fullest frame the stream ever
    showed — which for a terminal that stays open resurrects the output of a command three
    prompts ago every time something clears the screen.
    """
    terminal = _open()
    output, _ = await terminal.send(
        "echo BEFORE-THE-CLEAR; clear; echo after-the-clear", timeout=SEND_TIMEOUT
    )
    assert "after-the-clear" in output
    assert "BEFORE-THE-CLEAR" not in output
    assert "\x1b" not in output


# --------------------------------------------------------------------------- #
# Knowing when a send is done
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_terminal_going_quiet_does_not_mean_the_command_finished(session):
    """`idle` is evidence, and the tempting reading of it is proof.

    A persistent shell never exits, so silence is all there is to go on — and a command
    that pauses in the middle looks exactly like one that ended. The reason is reported so
    the agent can tell them apart; the output that arrives afterwards is kept, so acting
    on the wrong reading is recoverable rather than a permanent loss.
    """
    terminal = _open()
    # Quiet for well past the idle threshold, then speaks again. The line is arithmetic
    # so that the terminal's echo of the command typed is not itself a match for it.
    output, reason = await terminal.send('sleep 1.5; echo "late-$((6*7))"', timeout=SEND_TIMEOUT)
    assert reason is WaitReason.IDLE
    assert "late-42" not in output

    await asyncio.sleep(2.5)
    text, _ = terminal.read()
    assert "late-42" in text, "output produced after the send returned was dropped"


@pytest.mark.asyncio
async def test_a_send_that_runs_out_of_time_says_so_rather_than_looking_complete(session):
    """A truncated result presented as a finished one is worse than no result.

    The command keeps running either way; what the agent needs is to know that what it is
    holding is a prefix.
    """
    terminal = _open()
    # Never goes quiet: something is printed every 0.1s for longer than the wait allows.
    output, reason = await terminal.send(
        "for i in $(seq 1 40); do echo tick-$i; sleep 0.1; done", timeout=1.0
    )
    assert reason is WaitReason.TIMEOUT
    assert "tick-1" in output


@pytest.mark.asyncio
async def test_two_sends_at_once_are_refused_rather_than_interleaved(session):
    """Two writers on one terminal produce a line neither of them typed.

    Queueing the second would be worse than refusing it: the agent would be told its input
    was accepted, and the command it thought it ran would execute minutes later against a
    directory it no longer expects.
    """
    terminal = _open()
    first = asyncio.create_task(
        terminal.send("for i in $(seq 1 40); do echo tick-$i; sleep 0.1; done", timeout=1.0)
    )
    await asyncio.sleep(0.2)
    with pytest.raises(TerminalBusy):
        await terminal.send("echo interleaved", timeout=1.0)
    await first


# --------------------------------------------------------------------------- #
# Interrupting
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_interrupt_hits_the_running_command_and_leaves_the_shell(session):
    """Signalling the shell's own group instead would take the session's state with it.

    That is the mistake worth guarding: the shell is the only process whose pid is easy to
    reach, and using it means every ctrl-C ends the terminal the agent was building state
    in.
    """
    terminal = _open()
    await terminal.send('python3 -c "import time; time.sleep(30)"', timeout=SEND_TIMEOUT)
    shell_pgid = os.getpgid(terminal.pid)

    signalled = terminal.send_signal("SIGINT")
    assert signalled != shell_pgid, "the signal went to the shell, not to what was running"

    output, _ = await terminal.send('echo "still-here-$((6*7))"', timeout=SEND_TIMEOUT)
    assert "still-here-42" in output
    assert terminal.status is TerminalStatus.RUNNING


@pytest.mark.asyncio
async def test_a_kill_aimed_at_an_idle_terminal_is_refused(session):
    """With nothing running, the foreground group *is* the shell.

    Delivering the signal there would close the terminal while reporting that a command
    was stopped — the agent loses its state and is told the opposite of what happened.
    """
    terminal = _open()
    await terminal.send("echo idle-now", timeout=SEND_TIMEOUT)
    with pytest.raises(ValueError, match="terminal_close_tool"):
        terminal.send_signal("SIGKILL")
    assert terminal.status is TerminalStatus.RUNNING


# --------------------------------------------------------------------------- #
# Reaping
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_closing_a_terminal_takes_what_it_started_with_it(session):
    """A backgrounded job sits in its own process group, so killing the shell alone leaves it.

    This is the leak the module exists to prevent, and it is invisible from the agent's
    side: the terminal is gone from every listing while the process it started keeps
    running with nothing able to name it or stop it.
    """
    terminal = _open()
    output, _ = await terminal.send("sleep 60 & echo started-$!", timeout=SEND_TIMEOUT)
    # $! is namespace-local now. Observe the owned child using its host identity.
    import psutil
    child_pid = next(child.pid for child in psutil.Process(terminal.pid).children(recursive=True)
                     if child.name() == "sleep")
    assert await _alive(child_pid, deadline=0.2)

    terminal_manager.close(terminal.id)
    assert not await _alive(child_pid), f"pid {child_pid} outlived the terminal that started it"


@pytest.mark.asyncio
async def test_forgetting_a_session_ends_its_terminals(session):
    """A run that finishes without this leaves shells nothing can reach.

    The session is what could name them; once it is over, the terminals are unreachable
    through every tool the agent has, and they keep running.
    """
    first, second = _open(name="a"), _open(name="b")
    pids = [first.pid, second.pid]

    terminal_manager.forget(SESSION)
    assert terminal_manager.list(SESSION) == []
    for pid in pids:
        assert not await _alive(pid)


@pytest.mark.asyncio
async def test_a_running_terminal_is_never_evicted_to_make_room(session):
    """The cap must refuse the new terminal, not reclaim a live one.

    Evicting the oldest is the obvious way to hold a cap and the wrong one here: that
    terminal may be running a build, and killing it destroys work nobody asked to stop
    while the agent is told its new terminal opened fine.
    """
    opened = [_open() for _ in range(MAX_LIVE_PER_SESSION)]
    with pytest.raises(RuntimeError, match="terminal_close_tool"):
        _open()
    assert all(t.status is TerminalStatus.RUNNING for t in opened)
    assert len(terminal_manager.list(SESSION)) == MAX_LIVE_PER_SESSION


@pytest.mark.asyncio
async def test_a_terminal_that_exits_on_its_own_stays_listed(session):
    """What a shell printed as it died is usually the explanation for why it died.

    Dropping it from the registry the moment it exits leaves the agent with a terminal id
    that reports "no such terminal", which reads as its own mistake rather than as the
    shell having gone.
    """
    terminal = _open()
    await terminal.send("echo about-to-leave; exit", timeout=SEND_TIMEOUT)
    for _ in range(30):
        if terminal.status.is_final:
            break
        await asyncio.sleep(0.1)
    assert terminal.status is TerminalStatus.EXITED
    assert terminal in terminal_manager.list(SESSION)
    text, _ = terminal.read()
    assert "about-to-leave" in text


# --------------------------------------------------------------------------- #
# The actions
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_actions_carry_state_from_one_call_to_the_next(session, tmp_path):
    """The end-to-end claim, through the surface the agent actually has.

    Six tools before; one environment now. What is asserted did not change, because the
    claim never was about tools — it is that the shell stays where it was left, which is
    the only reason any of this exists.
    """
    from agentevolver.environment.default.terminal import TerminalEnvironment

    env = TerminalEnvironment()
    opened = await env.open(name="work", cwd=str(tmp_path), ctx=_Ctx())
    assert opened["success"]
    terminal_id = opened["terminal_id"]

    listed = await env.list(ctx=_Ctx())
    assert terminal_id in listed["message"]

    await env.send(terminal_id=terminal_id, text="MARK=persisted", ctx=_Ctx())
    read_back = await env.send(terminal_id=terminal_id, text='echo "mark=$MARK"', ctx=_Ctx())
    assert "mark=persisted" in read_back["message"]
    assert read_back["wait_reason"] in {r.value for r in WaitReason}

    closed = await env.close(terminal_id=terminal_id, ctx=_Ctx())
    assert closed["success"]
    assert terminal_manager.get(terminal_id) is None, (
        "a closed terminal stayed in the registry, so the agent can still type at it"
    )


@pytest.mark.asyncio
async def test_every_live_terminal_shows_up_in_the_state(session, tmp_path):
    """What makes this an environment rather than six tools.

    `terminal_read_tool` existed because a tool cannot volunteer anything: the agent had
    to remember to look, and one that forgets is typing into a shell it has not seen since
    two commands ago. The state arrives every step instead.
    """
    from agentevolver.environment.default.terminal import TerminalEnvironment

    env = TerminalEnvironment()
    first = (await env.open(name="alpha", cwd=str(tmp_path), ctx=_Ctx()))["terminal_id"]
    second = (await env.open(name="beta", cwd=str(tmp_path), ctx=_Ctx()))["terminal_id"]
    await env.send(terminal_id=first, text="echo alpha-marker", ctx=_Ctx())

    body = (await env.get_state(ctx=_Ctx()))["state"]
    assert "alpha" in body and "beta" in body, "both open terminals must be visible"
    assert "alpha-marker" in body, "the state must show what a terminal printed"

    await env.close(terminal_id=first, ctx=_Ctx())
    after = (await env.get_state(ctx=_Ctx()))["state"]
    assert first not in after, "a closed terminal has nothing live to show"
    assert second in after

    await env.close(terminal_id=second, ctx=_Ctx())
    assert (await env.get_state(ctx=_Ctx()))["state"] == "", (
        "with nothing open the state is empty, so the block is omitted entirely"
    )


@pytest.mark.asyncio
async def test_a_terminal_that_died_on_its_own_says_so_in_the_state(session, tmp_path):
    """The state change most worth volunteering.

    A build terminal that exits between two steps is invisible otherwise until something
    types into it and gets `exited` back — and by then the agent has planned a step around
    a shell that was not there. `close` removes a terminal from the registry outright, so
    this is only ever about one that ended by itself.
    """
    from agentevolver.environment.default.terminal import TerminalEnvironment

    env = TerminalEnvironment()
    terminal_id = (await env.open(name="doomed", cwd=str(tmp_path), ctx=_Ctx()))["terminal_id"]
    await env.send(terminal_id=terminal_id, text="exit", ctx=_Ctx())

    import asyncio as _asyncio

    deadline = 15.0
    while deadline > 0:
        live = terminal_manager.get(terminal_id)
        if live is not None and live.status.is_final:
            break
        await _asyncio.sleep(0.1)
        deadline -= 0.1
    else:
        raise AssertionError("the shell never registered as exited")

    body = (await env.get_state(ctx=_Ctx()))["state"]
    assert terminal_id in body, "a terminal that died on its own vanished from the state"
    assert "EXITED" in body


@pytest.mark.asyncio
async def test_reading_a_terminal_does_not_type_into_it(session):
    """Output arrives without being asked for; looking must not disturb the thing looked at.

    An agent whose only way to see a running build is to send something has to interrupt
    the build to read it.
    """
    from agentevolver.environment.default.terminal import TerminalEnvironment

    terminal = _open()
    await terminal.send("echo watched-output", timeout=SEND_TIMEOUT)
    before, _ = terminal.read()

    result = await TerminalEnvironment().read(terminal_id=terminal.id, ctx=_Ctx())
    assert result["success"]
    assert "watched-output" in result["message"]
    after, _ = terminal.read()
    assert after == before


@pytest.mark.asyncio
async def test_an_unknown_terminal_id_names_the_ones_that_exist(session):
    """A bare "not found" leaves the agent guessing at a handle it is already holding."""
    from agentevolver.environment.default.terminal import TerminalEnvironment

    terminal = _open()
    result = await TerminalEnvironment().send(terminal_id="term_nope", text="ls", ctx=_Ctx())
    assert not result["success"]
    assert terminal.id in result["message"]
