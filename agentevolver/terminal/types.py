"""What a live terminal is: a program holding a pty, and one round of typing at it.

A terminal is not a command. A command is started, produces output, and is over; a
terminal is a place the agent types into repeatedly, whose entire value is the state left
behind between calls — the working directory, the activated environment, the ssh hop, the
REPL that still holds the objects it built. That state has no representation here beyond
"the process is still alive and its screen is still ours", which is precisely why the
process must be owned rather than fired off.

The screen is rendered by `agentevolver.utils.terminal`, which knows nothing about who
produced the bytes. This file owns the process, the reader thread, and the question a
persistent terminal raises and a one-shot command does not: when has what I typed
finished, given that nothing exits to tell me?
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import subprocess
import termios
import threading
import time
from enum import Enum
from typing import List, Optional

from agentevolver.logger import logger
from agentevolver.utils.terminal import (
    PTY_DEFAULT_TERM,
    LiveScreen,
    open_pty,
)

#: How long the terminal must go quiet before a send is called settled. There is no exit
#: to wait for — the shell outlives every command — so silence is the only evidence
#: available that the thing typed is done. Too short and a command that pauses mid-output
#: returns half its result; too long and every interaction pays for the slowest one.
IDLE_SECONDS = 0.4

#: How often the settle loop looks. Below the idle threshold by enough that the reported
#: quiet period is roughly the real one.
POLL_SECONDS = 0.05

#: Ceiling on one send when the caller names none. Deliberately short: a send that hits
#: this is not lost — the terminal keeps running and its output keeps accumulating, so the
#: agent reads the rest with a second call rather than sitting through the first.
DEFAULT_SEND_TIMEOUT = 30.0

#: Signals the agent may deliver to whatever holds the terminal's foreground.
ALLOWED_SIGNALS = ("SIGINT", "SIGTERM", "SIGQUIT", "SIGHUP", "SIGTSTP", "SIGCONT", "SIGKILL")


class TerminalStatus(str, Enum):
    """Whether the program behind the terminal is still there."""

    RUNNING = "running"
    EXITED = "exited"

    @property
    def is_final(self) -> bool:
        return self is TerminalStatus.EXITED


class WaitReason(str, Enum):
    """Why a send stopped waiting.

    None of these means "the command exited": a persistent terminal has no such signal to
    give, short of the shell itself dying. ``IDLE`` means the terminal went quiet, which
    is evidence and not proof — a command that pauses for longer than the idle threshold
    looks exactly like one that finished. The agent has to be told which of these it got,
    or it will read a partial result as a complete one.
    """

    IDLE = "idle"
    TIMEOUT = "timeout"
    EXITED = "exited"


class TerminalBusy(RuntimeError):
    """Raised when a send arrives while another is still waiting on the same terminal.

    Two writers on one terminal interleave their keystrokes into a line neither typed.
    """


class Terminal:
    """One pty and the program holding it, alive across tool calls."""

    def __init__(self, *, id: str, name: str = "", session_id: str = "",
                 command: Optional[str] = None, cwd: Optional[str] = None) -> None:
        self.id = id
        self.name = name
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.status = TerminalStatus.RUNNING
        self.exit_code: Optional[int] = None
        self.started_at = time.monotonic()
        self.ended_at: Optional[float] = None
        self.pid: Optional[int] = None

        self.screen = LiveScreen()
        self.last_output_at = time.monotonic()

        self._master = -1
        self._process: Optional[subprocess.Popen] = None
        self._reader = None
        self._closed = False
        # Guards the screen and the last-output clock, both written by the reader thread
        # and read by whichever call is waiting for the terminal to go quiet.
        self._lock = threading.Lock()
        # Held for the duration of a send, so a second one is refused rather than queued.
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Attach a program to a fresh pty and begin draining it.

        The default program is an interactive bash with no rc files. Interactive because
        a shell that does not think it is interactive prints no prompt, disables job
        control, and reads its whole input at once — none of which is a terminal. Without
        rc files because whatever a user's profile prints would land in the screen the
        agent reads, and would differ from machine to machine.
        """
        env = {**os.environ, "TERM": PTY_DEFAULT_TERM}

        master, slave = open_pty()
        try:
            popen_args: dict = {}
            if self.command:
                argv, popen_args["shell"] = self.command, True
            else:
                argv = ["/bin/bash", "--norc", "--noprofile", "-i"]
            self._process = subprocess.Popen(
                argv,
                stdin=slave, stdout=slave, stderr=slave,
                cwd=self.cwd, env=env, close_fds=True,
                # Its own session, then its own controlling terminal. The session is what
                # keeps a signal aimed at this terminal away from the agent process; the
                # controlling terminal is what gives the shell job control, without which
                # every command runs in the shell's own process group and the foreground
                # group a signal should go to does not exist.
                start_new_session=True,
                preexec_fn=_take_controlling_terminal,
                **popen_args,
            )
        except BaseException:
            # Nothing else holds this end yet, and an fd nobody closes is a leak with no
            # terminal attached to explain it.
            os.close(master)
            raise
        finally:
            os.close(slave)

        self._master = master
        self.pid = self._process.pid
        self._reader = threading.Thread(
            target=self._read_loop, name=f"terminal-{self.id}", daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------
    # Draining
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Drain the pty for as long as it lives.

        Draining continuously, rather than only while a send is waiting, is not an
        optimisation. A pty buffer that nobody empties stops the program writing to it,
        so a terminal left alone between calls does not merely lose output — it freezes
        the program, which looks from outside exactly like a slow command.
        """
        while True:
            try:
                data = os.read(self._master, 65536)
            except OSError:
                break            # EIO once the last slave fd is gone, i.e. it exited
            if not data:
                break
            with self._lock:
                try:
                    self.screen.feed(data)
                except Exception as error:                          # noqa: BLE001
                    # An emulator that chokes on one escape sequence must not take the
                    # terminal with it; the session stays usable and the rest renders.
                    logger.warning(f"| 🖥️ Terminal {self.id} could not render a chunk: {error}")
                self.last_output_at = time.monotonic()
        self._mark_exited()

    def _mark_exited(self) -> None:
        process = self._process
        code = None
        if process is not None:
            code = process.poll()
            if code is None:
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    code = None
        with self._lock:
            if self.status is TerminalStatus.RUNNING:
                self.status = TerminalStatus.EXITED
                self.exit_code = code
                self.ended_at = time.monotonic()

    # ------------------------------------------------------------------
    # Typing
    # ------------------------------------------------------------------

    async def send(self, text: str = "", submit: bool = True,
                   timeout: float = DEFAULT_SEND_TIMEOUT,
                   idle: float = IDLE_SECONDS) -> tuple:
        """Type into the terminal and wait for it to settle. Returns (output, reason).

        The output is what appeared *because of this send*: lines that scrolled away
        since the write, plus the part of the current screen that changed. Returning the
        whole screen instead would re-serve the last three commands' output on every
        call, and returning only scrolled-off lines would return nothing at all for the
        common case where the answer fits on screen.
        """
        if self.status.is_final:
            raise RuntimeError(
                f"terminal {self.id} has exited (code {self.exit_code}); open a new one")
        if not self._send_lock.acquire(blocking=False):
            raise TerminalBusy(
                f"terminal {self.id} is still waiting on an earlier send")
        try:
            with self._lock:
                history_before, display_before = self.screen.frame()
                # The clock is reset at the write, not left where the last output put it.
                # Otherwise a terminal that has been idle for a minute is already "quiet"
                # the instant the loop below starts, and the send returns before the
                # command it just typed has drawn a single character.
                self.last_output_at = time.monotonic()
            marker = len(history_before)

            payload = (f"{text}\r" if submit else text).encode("utf-8", "replace")
            # Written to exhaustion. A pty accepts a few kilobytes at a time, so one
            # `os.write` of a heredoc or a long pipeline delivers a prefix — and the shell
            # then runs a command that was cut in half, which is far worse than an error.
            while payload:
                payload = payload[os.write(self._master, payload):]

            deadline = time.monotonic() + timeout
            reason = WaitReason.TIMEOUT
            while True:
                await asyncio.sleep(POLL_SECONDS)
                if self.status.is_final:
                    reason = WaitReason.EXITED
                    break
                now = time.monotonic()
                with self._lock:
                    quiet_for = now - self.last_output_at
                if quiet_for >= idle:
                    reason = WaitReason.IDLE
                    break
                if now >= deadline:
                    break

            with self._lock:
                history_after, display_after = self.screen.frame()
            return _delta(history_before, display_before,
                          history_after, display_after, marker), reason
        finally:
            self._send_lock.release()

    # ------------------------------------------------------------------
    # Looking
    # ------------------------------------------------------------------

    def read(self, offset: int = 0, count: int = 200) -> tuple:
        """A page of retained output, counted back from the newest line. Returns (text, total).

        Reading is separate from sending because a terminal produces output nobody asked
        for — a build that keeps printing, a server logging, a program that was left
        running. An agent that can only look by typing has to type something harmless
        into a terminal it may not want to disturb.
        """
        with self._lock:
            lines = self.screen.lines()
        total = len(lines)
        end = max(0, total - max(0, offset))
        start = max(0, end - max(1, count))
        return "\n".join(lines[start:end]), total

    def foreground_pgid(self) -> int:
        """Process group currently owning the terminal, or the shell's own as a fallback."""
        try:
            pgid = os.tcgetpgrp(self._master)
        except OSError:
            pgid = -1
        if pgid > 0:
            return pgid
        return os.getpgid(self._process.pid) if self._process else -1

    def send_signal(self, name: str) -> int:
        """Signal whatever currently holds the terminal's foreground. Returns the pgid.

        The foreground group, not the shell: the point of the signal is to reach the
        command that is running — the thing ctrl-C would hit — and the shell is what must
        survive to be typed at afterwards. Signalling the shell's group instead would take
        the session down and lose every bit of state it exists to hold.
        """
        if name not in ALLOWED_SIGNALS:
            raise ValueError(f"{name} is not one of {', '.join(ALLOWED_SIGNALS)}")
        if self.status.is_final:
            raise RuntimeError(f"terminal {self.id} has already exited")

        pgid = self.foreground_pgid()
        if pgid <= 0:
            raise RuntimeError(f"terminal {self.id} has no foreground process group")
        shell_pgid = os.getpgid(self._process.pid) if self._process else -1
        if name in ("SIGKILL", "SIGTERM", "SIGHUP") and pgid == shell_pgid:
            # Nothing is running, so the signal would land on the shell itself. That is
            # not a stronger interrupt, it is closing the terminal — which has its own
            # call, and which the agent should be doing on purpose.
            raise ValueError(
                f"{name} would hit the shell itself, since nothing is running in "
                f"terminal {self.id}; use terminal_close_tool to end the session")
        os.killpg(pgid, getattr(signal, name))
        return pgid

    # ------------------------------------------------------------------
    # Ending
    # ------------------------------------------------------------------

    def close(self) -> bool:
        """End the terminal and everything it is running. Idempotent; True the first time.

        Hang-up before kill, because the shell is the only thing that knows what it
        started. With job control on, each command bash runs sits in its own process
        group, so signalling bash's group alone leaves a build or a server running with
        nothing left to report on it or stop it — the exact leak this module exists to
        prevent. SIGHUP asks bash to hang up its jobs first; the master closing behind it
        hangs up anything that still holds the terminal open.
        """
        if self._closed:
            return False
        self._closed = True

        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGHUP)
            except OSError:
                process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except OSError:
                    process.kill()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    logger.warning(f"| ⚠️ Terminal {self.id} did not die after SIGKILL")

        if self._reader is not None:
            self._reader.join(timeout=2.0)
        if self._master >= 0:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = -1
        self._mark_exited()
        return True

    # ------------------------------------------------------------------
    # Describing
    # ------------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return (self.ended_at or time.monotonic()) - self.started_at

    def snapshot(self) -> dict:
        return {
            "terminal_id": self.id,
            "name": self.name,
            "status": self.status.value,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "command": self.command or "bash -i",
            "cwd": self.cwd,
            "elapsed": round(self.elapsed, 1),
        }

    def summary(self) -> str:
        """One line, for a listing."""
        state = self.status.value
        if self.status is TerminalStatus.EXITED and self.exit_code is not None:
            state = f"exited({self.exit_code})"
        label = self.name or (self.command or "bash -i")
        return f"{self.id}  {state:<12} {self.elapsed:6.1f}s  {label[:60]}"


def _take_controlling_terminal() -> None:
    """Make the already-dup'ed stdin this process's controlling terminal.

    Runs in the forked child, after `start_new_session` has made it a session leader and
    after the pty slave has become fd 0. A session leader with no controlling terminal
    gets no job control: bash says "no job control in this shell", every command runs in
    bash's own process group, and `tcgetpgrp` has no foreground group to report — so
    there is nothing for a signal to aim at except the shell.

    Kept to two calls with everything imported already, because anything that allocates
    or takes a lock between fork and exec can deadlock against a lock some other thread
    held at the moment of the fork.
    """
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass                     # no job control is worse than nothing, not fatal


def _delta(history_before: List[str], display_before: List[str],
           history_after: List[str], display_after: List[str], marker: int) -> str:
    """What appeared on the terminal between two frames.

    Two halves, because the two halves of a screen change differently. Lines that scrolled
    into history since the write are new by definition. The screen itself is compared to
    what stood there before and only the changed tail is kept — otherwise every send would
    re-serve whatever was still on screen from earlier commands, and an agent reading a
    result would have no way to tell this command's output from the last one's.
    """
    # History rotates once it is full, which shifts the marker out from under us. Clamping
    # returns a few older lines rather than crashing or slicing from the wrong end.
    scrolled = history_after[min(marker, len(history_after)):]

    common = 0
    while (common < len(display_before) and common < len(display_after)
           and display_before[common] == display_after[common]):
        common += 1

    lines = scrolled + display_after[common:]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


__all__ = [
    "Terminal",
    "TerminalBusy",
    "TerminalStatus",
    "WaitReason",
    "ALLOWED_SIGNALS",
    "IDLE_SECONDS",
    "DEFAULT_SEND_TIMEOUT",
]
