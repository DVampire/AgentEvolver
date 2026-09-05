"""The process control block, and the safe points that make control possible.

A safe point is any place the process voluntarily hands control back to the kernel. That
is the whole definition, and it is deliberately the same one coroutines already use:

``gate()``   between steps of a turn — signals apply, queued messages are delivered to
             the agent's ``on_event``.
``recv()``   the process is explicitly waiting for a message — for a child's report, for
             a reply to an escalation, or, when idle, for its next turn.

Suspending or stopping anywhere else would cut a turn in half: the model has emitted
tool calls whose results are not all recorded yet, and a conversation in that shape is
rejected by strict provider validation on the next request. Confining control to these
two points is what removes the previous runtime's hand-rolled reordering of external
notes around an in-flight batch.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from agentevolver.hook.events import HookEvent
from agentevolver.logger import logger
from agentevolver.runtime.envelopes import Envelope, ReplyEnvelope, ReportEnvelope
from agentevolver.runtime.errors import Killed, ProcessDead, Stopped
from agentevolver.runtime.mailbox import Mailbox
from agentevolver.runtime.modes import InteractionMode, infer
from agentevolver.runtime.signals import Signal, SignalBox
from agentevolver.runtime.states import (
    LIVE,
    RESUMABLE_TO,
    ExitStatus,
    ProcessState,
    check_transition,
)

#: Turns whose results a process keeps. Enough for a parent to look a few releases
#: back; the complete record is Trace's job, not the process table's.
MAX_REMEMBERED_TURNS = 32


class Process:
    """One running agent: identity, state, channels, and kinship.

    Everything mutable about a run lives here or on the agent instance the kernel
    created for it. Nothing is keyed by agent *name*, which is what let the previous
    design store per-run state on a globally registered singleton.
    """

    def __init__(
        self,
        pid: str,
        agent: Any,
        *,
        kernel: Any,
        ctx: Any = None,
        name: str = "",
        parent_pid: str = "",
        session_id: str = "",
        resident: bool = False,
        brief: str = "",
        mode: Optional[InteractionMode] = None,
        thread_id: str = "",
        resume: bool = False,
    ) -> None:
        # -- identity
        self.pid = pid
        self.name = name or getattr(agent, "name", "agent")
        self.agent = agent
        self.ctx = ctx
        self.session_id = session_id or str(getattr(ctx, "id", "") or "")
        self.parent_pid = parent_pid
        self.resident = resident
        # A thread survives process replacement only when explicitly resumed. Agent
        # names are templates, never identities for three concurrent participants.
        self.thread_id = thread_id or self.session_id or pid
        self.resume_thread = resume
        #: The endpoint role this process was started as. Recorded rather than derived,
        #: so a listing and a log line can say "subscriber" instead of leaving a reader
        #: to reconstruct it from two flags.
        self.mode = InteractionMode(mode) if mode is not None else infer(resident, ())
        #: A resident subscriber's standing instruction. Prepended to every event-driven
        #: turn, so a broadcast does not have to restate what this process is for.
        self.brief = brief

        # -- state
        self.state: ProcessState = ProcessState.NEW
        self.exit_status: Optional[ExitStatus] = None
        self.error: str = ""
        self.resume_to: ProcessState = ProcessState.RUNNING
        self.started_at: float = time.time()
        self.ended_at: Optional[float] = None
        #: Completed turns. A one-shot process ends at 1; a resident one counts up.
        self.turns: int = 0
        #: What the most recent turn returned, whatever the agent's result type is.
        self.last_result: Any = None
        #: What each completed turn produced, keyed by 1-based turn number — the first
        #: turn is 1, which is also what `turns` reads after it. A resident
        #: subscriber's parent needs the result of a *particular* turn — the verdict on
        #: release three, not the latest one — and "latest" cannot answer that. Bounded
        #: by `MAX_REMEMBERED_TURNS`; the full history is in Trace.
        self.turn_results: Dict[int, str] = {}
        #: Whether each completed turn succeeded, same keying.
        self.turn_success: Dict[int, bool] = {}

        # -- channels
        self.mailbox = Mailbox(owner=pid)
        self.signals = SignalBox()
        # Local delivery receipts, not provider steering acknowledgements. No message
        # bodies are duplicated here; the mailbox/trace remain the message authority.
        self.deliveries: Dict[str, Dict[str, Any]] = {}

        # -- kernel-owned handles
        self._kernel = kernel
        self._task: Optional[asyncio.Task] = None
        self._exited: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def transition(self, target: ProcessState) -> None:
        """Move to ``target``, refusing anything outside the transition table."""
        if target is self.state:
            return
        check_transition(self.state, target)
        logger.debug(f"| ⚙️ [{self.name}:{self.pid[:8]}] {self.state.value} → {target.value}")
        self.state = target

    @property
    def alive(self) -> bool:
        return self.state in LIVE

    @property
    def exited(self) -> bool:
        return self.state is ProcessState.EXITED

    @property
    def busy(self) -> bool:
        """Mid-turn. Derived from the state rather than tracked beside it.

        The previous runtime carried this as its own flag, which is how it came to
        disagree with the status it was supposed to qualify.
        """
        return self.state is ProcessState.RUNNING

    def record_turn(self, index: int, result: Any) -> None:
        """Remember what one turn produced, for a parent that asks about that turn."""
        message = getattr(result, "message", None)
        self.turn_results[index] = str(message if message is not None else (result or ""))
        self.turn_success[index] = bool(getattr(result, "success", result is not None))
        while len(self.turn_results) > MAX_REMEMBERED_TURNS:
            oldest = min(self.turn_results)
            self.turn_results.pop(oldest, None)
            self.turn_success.pop(oldest, None)

    # ------------------------------------------------------------------
    # Safe points
    # ------------------------------------------------------------------

    async def gate(self) -> None:
        """Safe point inside a turn: apply signals, then deliver queued messages.

        Called by the agent's own loop at the top of every step. It is the single place
        a suspend takes hold, a stop unwinds, and an event reaches ``on_event``.

        Raises:
            Stopped: A graceful stop was signalled.
            Killed: A forced stop was signalled.
        """
        await self._honor_signals()
        while True:
            envelope = self.mailbox.take()
            if envelope is None:
                return
            await self._deliver(envelope)
            # A hook may have taken real time, and a stop may have arrived during it.
            await self._honor_signals()

    def record_delivery(self, envelope: Envelope, status: str) -> None:
        self.deliveries[envelope.id] = {"status": status, "at": time.time()}
        # Bound terminal receipts; never evict a still-queued message's receipt.
        terminal = [key for key, value in self.deliveries.items()
                    if value["status"] != "queued"]
        for key in terminal[:-128]:
            self.deliveries.pop(key, None)

    async def recv(self, timeout: Optional[float] = None) -> Optional[Envelope]:
        """Safe point that waits for one message and hands it back to the caller.

        Two callers, one mechanism. The kernel calls this while the process is IDLE, and
        the envelope it returns becomes the next turn. An agent calls it mid-run to wait
        for a child's report or for the reply to an escalation, and handles the envelope
        itself. Either way signals are honoured while parked, so a suspended or stopped
        process does not sit here waiting for a message that may never come.

        Returns:
            The next envelope, or None when ``timeout`` elapsed first.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            await self._honor_signals()
            envelope = self.mailbox.take()
            if envelope is not None:
                self.record_delivery(envelope, "received")
                return envelope
            if self.mailbox.closed:
                raise ProcessDead(f"process {self.pid} mailbox is closed")
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return None
            if not await self._wait_any(remaining):
                return None

    async def ask_parent(self, text: str, *, timeout: Optional[float] = None) -> Optional[str]:
        """Report a blocker to the parent and wait for its reply.

        This is the whole escalation mechanism: a report marked ``blocked``, then an
        ordinary wait for a message. No suspension registry, no key to resolve — the
        child is simply a process waiting at a safe point, which is what it was.

        Returns:
            The parent's reply text, or None if it never came.
        """
        if not self.parent_pid:
            return None
        report = ReportEnvelope(sender=self.pid, text=text, blocked=True)
        await self._kernel.send(self.parent_pid, report)
        while True:
            envelope = await self.recv(timeout=timeout)
            if envelope is None:
                return None
            if isinstance(envelope, ReplyEnvelope):
                return envelope.text
            # Anything else that arrives while blocked is still the agent's to see.
            await self._deliver(envelope)

    async def report(
        self, text: str, *, final: bool = False, exit_status: Optional[str] = None
    ) -> bool:
        """Tell the parent something. No-op for a process with no parent."""
        if not self.parent_pid:
            return False
        return await self._kernel.send(
            self.parent_pid,
            ReportEnvelope(
                sender=self.pid, text=text, final=final, exit_status=exit_status
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _honor_signals(self) -> None:
        """Apply whatever signal is pending. May hold, may unwind."""
        signal = self.signals.take()
        if signal is None:
            return
        reason = self.signals.reason
        if signal is Signal.KILL:
            raise Killed(reason or "killed")
        if signal is Signal.STOP:
            raise Stopped(reason or "stopped")
        if signal is Signal.RESUME:
            return  # nothing was held; a stray resume is not an error
        await self._hold()

    async def _hold(self) -> None:
        """Suspend here until resumed, stopped or killed."""
        self.resume_to = self.state if self.state in RESUMABLE_TO else ProcessState.RUNNING
        self.transition(ProcessState.SUSPENDED)
        logger.info(f"| ⏸️ [{self.name}:{self.pid[:8]}] suspended")
        await self._hook("on_suspend")
        # Announced as well as hooked. `on_suspend` is the agent's own chance to release
        # volatile resources; this is how anything outside the process learns it was
        # held. Without it a suspended run is indistinguishable in a trace from one that
        # simply stopped producing steps.
        await self._observe(HookEvent.ON_SUSPEND, {"held_at": self.resume_to.value})
        while True:
            await self.signals.arrived.wait()
            signal = self.signals.take()
            reason = self.signals.reason
            if signal is Signal.KILL:
                raise Killed(reason or "killed while suspended")
            if signal is Signal.STOP:
                raise Stopped(reason or "stopped while suspended")
            if signal is Signal.RESUME:
                break
            # A second suspend while already suspended changes nothing.
        self.transition(self.resume_to)
        logger.info(f"| ▶️ [{self.name}:{self.pid[:8]}] resumed")
        await self._hook("on_resume")
        await self._observe(HookEvent.ON_RESUME, {"resumed_to": self.state.value})

    async def _wait_any(self, timeout: Optional[float] = None) -> bool:
        """Park until a message or a signal arrives. False when ``timeout`` won."""
        waiters = [
            asyncio.ensure_future(self.mailbox.wait()),
            asyncio.ensure_future(self.signals.arrived.wait()),
        ]
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            return bool(done)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _deliver(self, envelope: Envelope) -> None:
        """Hand one message to the agent. An agent error here must not kill the run."""
        handler: Optional[Callable[..., Any]] = getattr(self.agent, "on_event", None)
        if handler is None:
            self.record_delivery(envelope, "unhandled")
            logger.debug(
                f"| 📭 [{self.name}:{self.pid[:8]}] dropped {envelope.summary()}: "
                "the agent defines no on_event"
            )
            return
        try:
            await handler(envelope, self)
            self.record_delivery(envelope, "delivered")
        except (Stopped, Killed):
            self.record_delivery(envelope, "interrupted")
            raise
        except asyncio.CancelledError:
            self.record_delivery(envelope, "interrupted")
            raise
        except Exception as error:  # noqa: BLE001 - a bad handler must not end the run
            self.record_delivery(envelope, "failed")
            logger.warning(
                f"| ⚠️ [{self.name}:{self.pid[:8]}] on_event failed for "
                f"{envelope.summary()}: {error}"
            )

    async def _observe(self, event: HookEvent, body: Dict[str, Any]) -> None:
        """Broadcast a process fact to observers. Never raises.

        Separate from ``_hook`` because the two answer different questions: ``_hook``
        asks *this agent* to do something about a phase, while this tells everything
        outside the process that the phase happened. An observer that cannot write must
        not be able to hold a process suspended.
        """
        from agentevolver.agent.loop.events import events

        await events.broadcast(
            event,
            {
                "agent_name": self.name,
                "task_id": self.pid,
                "session_id": self.session_id,
                "parent_session_id": self.parent_pid or None,
                **body,
            },
            ctx=self.ctx,
        )

    async def _hook(self, name: str, *args: Any) -> Any:
        """Call an optional agent hook. Control-flow signals still propagate."""
        handler = getattr(self.agent, name, None)
        if handler is None:
            return None
        try:
            result = handler(*args)
            return await result if asyncio.iscoroutine(result) else result
        except (Stopped, Killed):
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(f"| ⚠️ [{self.name}:{self.pid[:8]}] {name} failed: {error}")
            return None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _grants(self) -> Dict[str, List[str]]:
        """The allowlists this process was granted, as opposed to defaulted.

        Reads the marker the router writes, so a default derived from the agent's class
        field is not reported as a grant — the distinction is the whole point of the
        marker, and a listing that blurred it would say every restricted agent had been
        granted its own restriction.
        """
        extra = getattr(self.ctx, "extra", None)
        if not isinstance(extra, dict):
            return {}
        granted = extra.get("_granted_allowlists") or ()
        return {
            key: [str(item) for item in (extra.get(key) or ())]
            for key in granted
            if isinstance(extra.get(key), (list, tuple))
        }

    def snapshot(self) -> Dict[str, Any]:
        """A small, JSON-safe view for ``ps``-style listings and the UI."""
        return {
            "deliveries": {key: dict(value) for key, value in self.deliveries.items()},
            "pid": self.pid,
            "thread_id": self.thread_id,
            "name": self.name,
            "state": self.state.value,
            "exit_status": self.exit_status.value if self.exit_status else None,
            "parent": self.parent_pid,
            "session": self.session_id,
            "resident": self.resident,
            # The declared role, so `ps` reads it rather than inferring it from
            # `resident` and a topic list — the inference that call sites used to do by
            # hand, and got wrong.
            "mode": self.mode.value,
            "turns": self.turns,
            "busy": self.busy,
            "queued": len(self.mailbox),
            "topics": list(self._kernel.topics_of(self.pid)) if self._kernel else [],
            # What this process was granted beyond what its agent declares. A grant is
            # written into the process's own context, so without this there is no way to
            # answer "which processes hold the capability this run just evolved" — the
            # record exists but nothing surfaces it.
            "grants": self._grants(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Process({self.name}:{self.pid[:8]}, {self.state.value})"


__all__ = ["MAX_REMEMBERED_TURNS", "Process"]
