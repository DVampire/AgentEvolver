"""TerminalServer — the one registry that knows every live pty this process opened.

A terminal that outlives its tool call is a process the agent can no longer see unless
something is holding it. That is the whole reason this exists: not to make terminals
convenient, but to make them findable and killable. Every path that ends a terminal —
the agent asking, a session finishing, the process exiting — comes through here, because
a pty leaked by any one of them is a live shell nobody will ever reap.
"""

from __future__ import annotations

import atexit
import uuid
from typing import Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.terminal.types import Terminal
from agentevolver.utils import Singleton

#: Live terminals one session may hold at once. A cap exists because each one is a real
#: shell holding a pty and whatever it started; an agent that opens one per command would
#: accumulate them silently. Reaching it is not an eviction — see `open`.
MAX_LIVE_PER_SESSION = 8

#: Exited terminals kept per session. Their screens are still worth reading — what a shell
#: printed as it died is usually the explanation — but not indefinitely.
MAX_EXITED_PER_SESSION = 10


class TerminalServer(metaclass=Singleton):
    """Opens, tracks, and reaps persistent terminals."""

    def __init__(self) -> None:
        self._terminals: Dict[str, Terminal] = {}
        self._atexit_registered = False

    # ------------------------------------------------------------------
    # Opening
    # ------------------------------------------------------------------

    def open(self, *, name: str = "", session_id: str = "",
             command: Optional[str] = None, cwd: Optional[str] = None) -> Terminal:
        """Start a terminal and take ownership of it.

        Refuses rather than evicts once a session is at its live cap. A running terminal
        is holding state the agent asked for and may be running a command right now;
        dropping the oldest to make room would kill work that nothing recorded and that
        nobody asked to stop. The agent is told to close one, which is a decision it can
        make with what it knows and this registry cannot.
        """
        live = [t for t in self.list(session_id) if not t.status.is_final]
        if len(live) >= MAX_LIVE_PER_SESSION:
            raise RuntimeError(
                f"{len(live)} terminals are already open in this session "
                f"(the limit is {MAX_LIVE_PER_SESSION}); close one with "
                f"terminal_close_tool before opening another")
        if name and any(t.name == name for t in live):
            raise ValueError(
                f"a terminal named {name!r} is already open in this session")

        terminal = Terminal(id=f"term_{uuid.uuid4().hex[:8]}", name=name,
                            session_id=session_id, command=command, cwd=cwd)
        try:
            terminal.start()
        except Exception:
            # A start that failed half-way may still have a pty and a process behind it,
            # and nothing has its id yet — this is the one moment a terminal can leak
            # while no registry entry exists to find it by.
            terminal.close()
            raise
        self._terminals[terminal.id] = terminal
        self._evict(session_id)
        self._arm_process_exit()
        logger.info(f"| 🖥️ Terminal {terminal.id} opened "
                    f"({terminal.command or 'bash -i'}) pid={terminal.pid}")
        return terminal

    # ------------------------------------------------------------------
    # Finding
    # ------------------------------------------------------------------

    def get(self, terminal_id: str) -> Optional[Terminal]:
        return self._terminals.get(terminal_id)

    def list(self, session_id: str = "") -> List[Terminal]:
        """One session's terminals, oldest first; every terminal when no session is given.

        Oldest first, unlike the job listing: terminals are named by the order they were
        opened ("the first one", "the build one"), and a listing that reshuffles as they
        exit makes that reference unreliable.
        """
        terminals = [t for t in self._terminals.values()
                     if not session_id or t.session_id == session_id]
        return sorted(terminals, key=lambda t: t.started_at)

    # ------------------------------------------------------------------
    # Reaping
    # ------------------------------------------------------------------

    def close(self, terminal_id: str) -> bool:
        """Close one terminal and forget it. False if it was already gone.

        Forgotten, unlike a finished job: a job's record is its output, while a closed
        terminal's screen describes a session that no longer exists, and leaving it in the
        listing invites the agent to type into something that cannot answer. A terminal
        that exited on its own stays listed — there the screen is the explanation.
        """
        terminal = self._terminals.pop(terminal_id, None)
        if terminal is None:
            return False
        closed = terminal.close()
        logger.info(f"| 🖥️ Terminal {terminal_id} closed after {terminal.elapsed:.1f}s")
        return closed

    def forget(self, session_id: str) -> None:
        """End a session's terminals. Every one, running or not.

        This is the call that makes a session's terminals session-scoped rather than
        process-scoped. Without it a finished run leaves its shells behind, and they are
        no longer reachable through any tool: the session that could name them is over.
        """
        for terminal in self.list(session_id):
            self._terminals.pop(terminal.id, None)
            terminal.close()

    def close_all(self) -> None:
        """End every terminal. Last resort, wired to process exit."""
        for terminal in list(self._terminals.values()):
            self._terminals.pop(terminal.id, None)
            try:
                terminal.close()
            except Exception as error:                              # noqa: BLE001
                logger.warning(f"| ⚠️ Terminal {terminal.id} would not close: {error}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _arm_process_exit(self) -> None:
        """Close terminals when the process ends, and only once.

        A pty is not cleaned up by the interpreter shutting down: the shell is a child in
        its own session, and it survives its parent perfectly happily. Registered on the
        first open rather than at import, so a process that never opens one does not pay
        for the hook.
        """
        if self._atexit_registered:
            return
        atexit.register(self.close_all)
        self._atexit_registered = True

    def _evict(self, session_id: str) -> None:
        """Hold the exited-terminal count for one session.

        Only exited ones, and oldest first. A running terminal is never evicted however
        many there are — forgetting it would leave a live shell, and whatever it is
        running, with nothing able to name it, read it, or stop it.
        """
        exited = [t for t in self.list(session_id) if t.status.is_final]
        for terminal in exited[:-MAX_EXITED_PER_SESSION]:
            self._terminals.pop(terminal.id, None)
            terminal.close()


terminal_manager = TerminalServer()

__all__ = ["terminal_manager", "TerminalServer",
           "MAX_LIVE_PER_SESSION", "MAX_EXITED_PER_SESSION"]
