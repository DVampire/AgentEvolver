"""The kernel: the process table, the turn driver, and inter-process delivery.

It owns three things and no more — when a process may run, when a message reaches it,
and how it is created and reaped. What happens inside a turn belongs to the agent, and
the kernel never inspects it. That is why a model-driven agent, a deterministic
procedure and an orchestrator are all the same kind of thing here: each is an object
with a ``__call__`` and some optional hooks.

Two modes, one mechanism:

*dispatch*      ``spawn`` a child, then either ``wait`` for it or park in ``recv`` and
                collect the ``ReportEnvelope`` the kernel posts when it exits. This is
                fork and waitpid, including the part where the parent hears about the
                child without polling for it.
*subscription*  ``spawn(resident=True, topics=[...])``. The process registers IDLE
                without spending a turn, and each ``publish`` becomes one turn. Its
                conversation and memory persist between them.

The difference is one flag and one index. There is no second code path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from agentevolver.logger import logger
from agentevolver.runtime.envelopes import (
    Envelope,
    EventEnvelope,
    ReplyEnvelope,
    ReportEnvelope,
    TaskEnvelope,
)
from agentevolver.runtime.errors import (
    InvalidTransition,
    Killed,
    MailboxClosed,
    ProcessNotFound,
    Stopped,
    describe,
)
from agentevolver.runtime.modes import (
    InteractionMode,
    check_topics,
    infer,
    lifecycle,
)
from agentevolver.runtime.process import Process
from agentevolver.runtime.signals import Signal
from agentevolver.runtime.states import ExitStatus, ProcessState
from agentevolver.runtime.topics import TopicRegistry
from agentevolver.utils import make_id

#: How long a forced stop waits for the process task to unwind before giving up on it.
KILL_GRACE_SECONDS = 10.0

Target = Union[Process, str]


class Kernel:
    """Creates, schedules, connects and reaps agent processes."""

    def __init__(self) -> None:
        self._procs: Dict[str, Process] = {}
        self._topics = TopicRegistry()
        #: When each process was last handed work by `assign`. The round-robin half of
        #: competing consumers: without it an idle pool ties on every other key and one
        #: worker takes everything.
        self._assigned_at: Dict[str, float] = {}

    # ==================================================================
    # Process lifecycle
    # ==================================================================

    async def spawn(
        self,
        agent: Any,
        task: str = "",
        *,
        files: Optional[Sequence[str]] = None,
        ctx: Any = None,
        parent: Optional[Target] = None,
        mode: Optional[InteractionMode] = None,
        resident: bool = False,
        topics: Sequence[str] = (),
        name: str = "",
        start_idle: Optional[bool] = None,
        **kwargs: Any,
    ) -> Process:
        """Create a process for ``agent`` and start driving it.

        Args:
            agent: The instance to run. The kernel binds it to its process as ``.proc``;
                that handle is the agent's only door back into the kernel.
            task: First turn's work, or — for a subscriber — the standing brief.
            resident: Park IDLE after each turn instead of exiting.
            topics: Subscriptions. Naming any implies ``resident`` and ``start_idle``,
                because a subscriber's work arrives later by definition.
            start_idle: Override that inference. ``False`` runs ``task`` immediately even
                for a subscriber.

        Returns:
            The live :class:`Process`. It is already running; nothing needs to be awaited
            unless the caller wants the result.
        """
        parent_proc = self._resolve(parent, required=False) if parent else None
        # One place decides what each mode means. Callers used to assemble `resident`,
        # `start_idle` and `topics` by hand, and a wrong combination raised nothing —
        # it produced a process that looked spawned and did nothing. The old flags stay
        # accepted and are mapped onto the mode they meant, so there is one
        # implementation rather than two spellings that can drift.
        mode = InteractionMode(mode) if mode is not None else infer(resident, topics)
        check_topics(mode, topics)
        shape = lifecycle(mode)
        resident = shape.resident
        if start_idle is None:
            start_idle = shape.start_idle

        pid = make_id()
        proc = Process(
            pid,
            agent,
            kernel=self,
            ctx=ctx,
            name=name or getattr(agent, "name", "agent"),
            parent_pid=parent_proc.pid if parent_proc else "",
            session_id=str(getattr(ctx, "id", "") or ""),
            resident=resident,
            brief=task if start_idle else "",
            mode=mode,
        )
        self._procs[pid] = proc
        if topics:
            # Scoped with the SAME function the publisher uses. Subscribing under the
            # raw name while `publish_scoped` looks up `{root}::{name}` is a silent
            # no-match: the fan-out reports 0 subscribers and every resident process
            # waits forever for an event that was delivered to nobody. Measured on a
            # live run — four subscribers registered, `📡 publish … → 0 subscriber(s)`.
            self._topics.subscribe_many(pid, self._scope_all(topics, ctx))

        # The agent's handle on its own process. Everything an agent can ask of the
        # kernel goes through this one attribute, so the dependency is visible.
        try:
            agent.proc = proc
        except Exception as error:  # noqa: BLE001 - frozen models are still runnable
            logger.debug(f"| ⚙️ could not bind proc onto {proc.name}: {error}")

        # And the same handle for code that only ever receives a context: a tool is given
        # a ToolContext derived from this one, and `escalate_tool` has to reach its own
        # process to ask a parent. The pid rather than the object, so a context stays
        # copyable and a stale one names a process the table can simply not find.
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra["process_pid"] = pid
            if proc.parent_pid:
                extra.setdefault("parent_process_pid", proc.parent_pid)

        first = TaskEnvelope(
            sender=proc.parent_pid,
            task=task or "",
            files=list(files or []),
            kwargs=dict(kwargs),
        )
        proc._task = asyncio.create_task(
            self._serve(proc, None if start_idle else first),
            name=f"proc-{pid[:8]}-{proc.name}",
        )
        logger.info(
            f"| 🚀 [{proc.name}:{pid[:8]}] spawned"
            + (f" resident topics={list(topics)}" if topics else "")
            + (f" parent={proc.parent_pid[:8]}" if proc.parent_pid else "")
        )
        return proc

    async def wait(self, target: Target, timeout: Optional[float] = None) -> Any:
        """Block until the process exits; return whatever its last turn produced.

        Read ``proc.exit_status`` for how it ended. This is the blocking half of
        dispatch; the non-blocking half is doing nothing and letting the final
        ``ReportEnvelope`` arrive in your own mailbox.

        Raises:
            asyncio.TimeoutError: ``timeout`` elapsed while it was still running.
        """
        proc = self._resolve(target)
        if not proc.exited:
            await asyncio.wait_for(proc._exited.wait(), timeout=timeout)
        return proc.last_result

    async def stop(
        self, target: Target, *, force: bool = False, reason: str = ""
    ) -> bool:
        """Ask a process to end. Returns False when it had already exited.

        ``force`` skips the landing hook and cancels the task outright, so a process
        parked inside a long model call stops now rather than at its next step.
        """
        proc = self._resolve(target, required=False)
        if proc is None or proc.exited:
            return False
        signal = Signal.KILL if force else Signal.STOP
        proc.signals.raise_signal(signal, reason)
        logger.info(
            f"| 🛑 [{proc.name}:{proc.pid[:8]}] {signal.value}"
            + (f": {reason}" if reason else "")
        )
        if force and proc._task is not None and not proc._task.done():
            proc._task.cancel()
        return True

    async def suspend(self, target: Target) -> bool:
        """Hold a process at its next safe point."""
        proc = self._resolve(target, required=False)
        if proc is None or proc.exited or proc.state is ProcessState.SUSPENDED:
            return False
        proc.signals.raise_signal(Signal.SUSPEND)
        return True

    async def resume(self, target: Target) -> bool:
        """Release a held process back to what it was doing."""
        proc = self._resolve(target, required=False)
        if proc is None or proc.exited:
            return False
        proc.signals.raise_signal(Signal.RESUME)
        return True

    # ==================================================================
    # Messaging
    # ==================================================================

    async def send(self, target: Target, envelope: Envelope) -> bool:
        """Deliver one message. False when the target is gone or already closed."""
        proc = self._resolve(target, required=False)
        if proc is None or not proc.alive:
            return False
        try:
            proc.mailbox.put(envelope)
        except MailboxClosed:
            return False
        return True

    async def send_task(self, target: Target, task: str, **kwargs: Any) -> bool:
        """Give a resident process its next turn."""
        return await self.send(target, TaskEnvelope(task=task, kwargs=dict(kwargs)))

    async def reply(self, target: Target, text: str, *, in_reply_to: str = "") -> bool:
        """Unblock a child that is waiting inside :meth:`Process.ask_parent`."""
        return await self.send(
            target, ReplyEnvelope(text=text, in_reply_to=in_reply_to)
        )

    @staticmethod
    def _scope_all(topics: Sequence[str], ctx: Any) -> List[str]:
        """Scope each topic to its task tree, falling back to the raw name.

        A caller with no session identity — a test, a bare kernel — keeps the plain
        name, so `publish`/`subscribe` still pair up outside a session.
        """
        from agentevolver.runtime.topics import scoped

        names: List[str] = []
        for topic in topics:
            try:
                names.append(scoped(topic, ctx))
            except ValueError:
                names.append(str(topic).strip())
        return names

    async def assign(
        self,
        topic: str,
        task: str,
        *,
        ctx: Any = None,
        **kwargs: Any,
    ) -> str:
        """Give this work to exactly ONE subscriber of ``topic``. Returns its pid, or "".

        The competing-consumers half of a topic — PUSH/PULL rather than PUB/SUB. Both
        indexes are the same one; what differs is the delivery discipline, and only this
        one asks "who is free" instead of "who is listening".

        Without it a pool of interchangeable workers was not expressible. `publish` fans
        out, so N workers each did the whole job; `send` needs a pid, so the caller had
        to choose, which is dispatch and not a pool. Anything that wants work spread
        across whoever is available — a queue of subtasks, a rate-limited resource with
        several holders — needed this and had to be faked by the caller picking.

        Idle first, then fewest queued. A busy process would take the work and sit on it
        until its current turn ends, which is the opposite of what a pool is for.
        """
        name = self._scope_all([topic], ctx)[0] if ctx is not None else topic
        candidates = [
            proc for proc in (self._procs.get(pid) for pid in self._topics.subscribers(name))
            if proc is not None and proc.alive
        ]
        if not candidates:
            logger.warning(
                f"| 📭 assign {name} reached nobody"
                + (f"; subscribed topics are {list(self._topics.all_topics())}"
                   if self._topics.all_topics() else "; nothing is subscribed")
            )
            return ""
        # Idle beats busy, then the shortest queue, then whoever was assigned longest
        # ago. Ranked rather than filtered so a pool whose every worker is busy still
        # takes the work instead of dropping it — a queue that refuses when everyone is
        # working is not a queue.
        #
        # That last tie-break is what makes it a pool. Without it an idle pool with an
        # empty queue is a three-way tie that `min` breaks the same way every time, so
        # one worker took every job and the other two never ran.
        chosen = min(
            candidates,
            key=lambda proc: (
                proc.busy, len(proc.mailbox), self._assigned_at.get(proc.pid, 0.0),
            ),
        )
        self._assigned_at[chosen.pid] = time.time()
        if not await self.send(chosen, TaskEnvelope(task=task, kwargs=dict(kwargs))):
            return ""
        logger.info(
            f"| 📥 assign {name} → {chosen.name}:{chosen.pid[:8]} "
            f"(of {len(candidates)} subscriber(s))"
        )
        return chosen.pid

    async def publish(
        self,
        topic: str,
        event_type: str = "",
        payload: Optional[Dict[str, Any]] = None,
        *,
        sender: str = "",
    ) -> int:
        """Fan an event out to every live subscriber. Returns how many accepted it."""
        delivered = 0
        for pid in self._topics.subscribers(topic):
            proc = self._procs.get(pid)
            if proc is None or not proc.alive:
                continue
            envelope = EventEnvelope(
                sender=sender, topic=topic, event_type=event_type,
                payload=dict(payload or {}),
            )
            if await self.send(proc, envelope):
                delivered += 1
        if delivered:
            logger.info(f"| 📡 publish {topic}/{event_type} → {delivered} subscriber(s)")
        else:
            # Loud, because the failure it catches is otherwise invisible: the call
            # succeeds, returns 0, and every resident subscriber waits forever. Naming
            # the topics that DO have subscribers is what makes a scope mismatch
            # readable at the moment it happens rather than hours into a run.
            known = self._topics.all_topics()
            logger.warning(
                f"| 📭 publish {topic}/{event_type} reached nobody"
                + (f"; subscribed topics are {list(known)}" if known
                   else "; nothing is subscribed")
            )
        return delivered

    def subscribe(self, target: Target, topic: str) -> bool:
        """Add one subscription, scoped to the process's own task tree.

        Scoped here for the same reason `spawn` scopes: there is exactly one rule for
        what a topic name means, and it is applied at every entry point. A second entry
        point that skipped it would reintroduce the silent no-match this cost once.
        """
        proc = self._resolve(target)
        return self._topics.subscribe(proc.pid, self._scope_all([topic], proc.ctx)[0])

    def unsubscribe(self, target: Target, topic: str) -> bool:
        proc = self._resolve(target)
        return self._topics.unsubscribe(proc.pid, self._scope_all([topic], proc.ctx)[0])

    def topics_of(self, pid: str) -> Sequence[str]:
        return self._topics.topics(pid)

    async def publish_scoped(
        self,
        topic: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        ctx: Any = None,
        sender: str = "",
    ) -> Tuple[int, str, EventEnvelope]:
        """Publish under the caller's own task tree.

        Returns the fan-out count, the scoped name, and the envelope that was sent — a
        caller that has to hand a receipt back to a model needs the event's identity.

        The scoping is here rather than at the call site because getting it wrong is
        invisible: an unscoped publish reaches another session's subscribers and reads
        like the model addressing the wrong thing.
        """
        from agentevolver.runtime.topics import scoped

        name = scoped(topic, ctx)
        event = EventEnvelope(
            sender=sender, topic=name, event_type=event_type,
            payload=dict(payload or {}),
        )
        delivered = 0
        for pid in self._topics.subscribers(name):
            proc = self._procs.get(pid)
            if proc is None or not proc.alive:
                continue
            if await self.send(proc, event):
                delivered += 1
        logger.info(f"| 📡 publish {name}/{event_type} → {delivered} subscriber(s)")
        return delivered, name, event

    # ==================================================================
    # Process table
    # ==================================================================

    def get(self, pid: str) -> Optional[Process]:
        return self._procs.get(pid)

    def list(
        self, *, session_id: str = "", alive_only: bool = True
    ) -> List[Process]:
        """Every process, newest last. One answer to "what is running"."""
        procs = [
            proc for proc in self._procs.values()
            if (not alive_only or proc.alive)
            and (not session_id or proc.session_id == session_id)
        ]
        return sorted(procs, key=lambda proc: proc.started_at)

    def children(self, target: Target) -> List[Process]:
        proc = self._resolve(target)
        return [
            child for child in self._procs.values() if child.parent_pid == proc.pid
        ]

    def snapshot(self) -> List[Dict[str, Any]]:
        """``ps`` for the process table."""
        return [proc.snapshot() for proc in self.list(alive_only=False)]

    async def shutdown(self, *, timeout: float = KILL_GRACE_SECONDS) -> None:
        """Stop every live process, forcefully, and wait for the table to empty."""
        live = [proc for proc in self._procs.values() if proc.alive]
        for proc in live:
            await self.stop(proc, force=True, reason="kernel shutdown")
        if live:
            await asyncio.wait(
                [asyncio.ensure_future(proc._exited.wait()) for proc in live],
                timeout=timeout,
            )
        self._procs.clear()

    def forget(self, *, session_id: str = "") -> int:
        """Drop exited processes from the table. Returns how many were removed."""
        gone = [
            pid for pid, proc in self._procs.items()
            if proc.exited and (not session_id or proc.session_id == session_id)
        ]
        for pid in gone:
            self._procs.pop(pid, None)
        return len(gone)

    # ==================================================================
    # The driver
    # ==================================================================

    async def _serve(self, proc: Process, first: Optional[TaskEnvelope]) -> None:
        """Drive one process from its first turn to its exit.

        One turn at a time, always. A resident process alternates RUNNING and IDLE here;
        nothing else may start a second turn on the same process, which is the property
        the previous runtime needed an extra queue and a separate driver coroutine to
        get.
        """
        status = ExitStatus.DONE
        reason = ""
        graceful = True
        try:
            await self._announce(proc, "start", first)
            envelope: Optional[Envelope] = first
            if envelope is None:
                proc.transition(ProcessState.IDLE)
                envelope = await proc.recv()
            else:
                proc.transition(ProcessState.RUNNING)
                await proc._hook("on_start", envelope.task, proc)

            started = first is not None
            while envelope is not None:
                if proc.state is not ProcessState.RUNNING:
                    proc.transition(ProcessState.RUNNING)
                if not started:
                    await proc._hook("on_start", self._input_text(proc, envelope), proc)
                    started = True

                proc.last_result = await self._turn(proc, envelope)
                proc.turns += 1
                proc.record_turn(proc.turns, proc.last_result)

                if not proc.resident:
                    break
                proc.transition(ProcessState.IDLE)
                envelope = await proc.recv()
        except Stopped as signal:
            status, reason = ExitStatus.CANCELLED, signal.reason
        except Killed as signal:
            status, reason, graceful = ExitStatus.CANCELLED, signal.reason, False
        except asyncio.CancelledError:
            status, reason, graceful = ExitStatus.CANCELLED, "cancelled", False
        except Exception as error:  # noqa: BLE001 - an agent fault is an exit, not a crash
            status, reason = ExitStatus.FAILED, describe(error)
            proc.error = reason
            logger.error(
                f"| 💥 [{proc.name}:{proc.pid[:8]}] failed: {reason}", exc_info=True
            )
        finally:
            await self._exit(proc, status, reason, graceful=graceful)

    async def _turn(self, proc: Process, envelope: Envelope) -> Any:
        """Run the agent once over one input."""
        task = self._input_text(proc, envelope)
        files = list(getattr(envelope, "files", ()) or ())
        kwargs = dict(getattr(envelope, "kwargs", {}) or {})
        logger.info(f"| ▶️ [{proc.name}:{proc.pid[:8]}] turn {proc.turns + 1}")
        return await proc.agent(task=task, files=files, ctx=proc.ctx, **kwargs)

    @staticmethod
    def _input_text(proc: Process, envelope: Envelope) -> str:
        """Render any envelope into the turn's task text.

        A subscriber's standing brief leads, because an event on its own does not say
        what this process is supposed to do about it.
        """
        parts: List[str] = []
        # The brief leads whatever woke the process, not only an event. A resident
        # process's brief IS its identity — the persona a co-design participant was
        # assigned, the standing instruction a watcher holds — and returning
        # `envelope.task` alone dropped it for every direct message. A subscriber woken
        # by `send_message` then answered "NO ASSIGNED CONTEXT", which reads like the
        # parent forgot to assign one rather than like the kernel discarding it.
        # A one-shot process has no brief, so this changes nothing for a plain dispatch.
        if proc.brief:
            parts.append(proc.brief)
        if isinstance(envelope, TaskEnvelope):
            parts.append(envelope.task)
        elif isinstance(envelope, EventEnvelope):
            body = "\n".join(
                f"{key}: {value}" for key, value in sorted(envelope.payload.items())
            )
            parts.append(
                f"<event topic=\"{envelope.topic}\" type=\"{envelope.event_type}\">\n"
                f"{body}\n</event>"
            )
        elif isinstance(envelope, ReportEnvelope):
            parts.append(
                f"<report from=\"{envelope.sender}\">\n{envelope.text}\n</report>"
            )
        elif isinstance(envelope, ReplyEnvelope):
            parts.append(f"<reply>\n{envelope.text}\n</reply>")
        else:  # pragma: no cover - future envelope kinds
            parts.append(envelope.summary())
        return "\n\n".join(part for part in parts if part)

    async def _exit(
        self, proc: Process, status: ExitStatus, reason: str, *, graceful: bool
    ) -> None:
        """The single exit path: land, mark, unsubscribe, reap, notify.

        Every ending goes through here — finished, failed, stopped, killed — so the
        clean-up and the parent notification exist once instead of once per outcome.
        """
        if proc.exited:
            return
        # Every await below can be interrupted — a forced stop cancels this very task
        # while it is unwinding. None of them may prevent the process from being marked
        # exited and its waiters woken, so each is guarded and `_exited` is set in a
        # finally. A process whose clean-up failed is still a process that ended.
        try:
            try:
                proc.transition(ProcessState.STOPPING)
            except InvalidTransition:  # pragma: no cover - already terminal
                pass

            if graceful:
                # The landing hook: the agent's one chance to persist a partial result.
                await self._guarded(
                    proc._hook("on_land", reason), proc, "landing (on_land)"
                )

            proc.exit_status = status
            proc.error = proc.error or (reason if status is ExitStatus.FAILED else "")
            proc.ended_at = time.time()
            proc.transition(ProcessState.EXITED)
            self._topics.drop(proc.pid)
            undelivered = proc.mailbox.close()
            proc.signals.clear()
            if undelivered:
                logger.info(
                    f"| 📭 [{proc.name}:{proc.pid[:8]}] exited with {len(undelivered)} "
                    f"undelivered message(s)"
                )

            await self._guarded(self._reap_children(proc), proc, "reaping children")

            if proc.parent_pid:
                await self._guarded(
                    self.send(
                        proc.parent_pid,
                        ReportEnvelope(
                            sender=proc.pid,
                            text=self._final_text(proc, reason),
                            final=True,
                            exit_status=status.value,
                        ),
                    ),
                    proc,
                    "notifying parent",
                )

            await self._guarded(proc._hook("on_exit", status), proc, "on_exit")
            await self._guarded(
                self._announce(proc, "exit", None, status=status), proc, "exit events"
            )
        finally:
            proc._exited.set()
            logger.info(
                f"| 🏁 [{proc.name}:{proc.pid[:8]}] exited "
                f"{(proc.exit_status or status).value}"
                + (f": {reason}" if reason else "")
            )

    @staticmethod
    async def _guarded(awaitable: Any, proc: Process, what: str) -> None:
        """Run one clean-up step; never let it abort the rest of the exit path."""
        try:
            await asyncio.shield(asyncio.ensure_future(awaitable))
        except asyncio.CancelledError:
            logger.warning(f"| ⚠️ [{proc.name}:{proc.pid[:8]}] {what} interrupted")
        except Exception as error:  # noqa: BLE001
            logger.warning(f"| ⚠️ [{proc.name}:{proc.pid[:8]}] {what} failed: {error}")

    @staticmethod
    async def _announce(
        proc: Process, phase: str, first: Optional[Envelope], *, status: Any = None
    ) -> None:
        """Publish process lifecycle to whoever subscribed.

        The kernel raises these because it is the only thing that knows a process has
        truly begun or truly ended — it owns the single exit path. Under the previous
        design the agent emitted them, so a deterministic agent that skipped the model
        loop had to re-emit the same pair itself, and did.

        A root process opens and closes a *session*; a spawned child opens and closes a
        *sub-agent*. They were both announced as SESSION_START before, so a run that
        dispatched four children reported five session starts and a listener counting
        sessions counted dispatches. `TASK_COMPLETED` is the one event both raise: every
        process runs a task, whoever spawned it.
        """
        from agentevolver.agent.loop.events import events
        from agentevolver.hook.types import HookEvent

        body = {
            "agent_name": proc.name,
            "task_id": proc.pid,
            "session_id": proc.session_id,
            "parent_session_id": proc.parent_pid or None,
        }
        child = bool(proc.parent_pid)
        if phase == "start":
            task = getattr(first, "task", "") if first is not None else proc.brief
            if child:
                await events.broadcast(
                    HookEvent.SUBAGENT_START, {**body, "task": task}, ctx=proc.ctx
                )
                return
            # A root process is a session, and its task is the prompt that opened it.
            await events.broadcast(
                HookEvent.USER_PROMPT_SUBMIT, {**body, "task": task}, ctx=proc.ctx
            )
            await events.broadcast(
                HookEvent.SESSION_START, {**body, "task": task}, ctx=proc.ctx
            )
            return
        outcome = getattr(status, "value", status)
        await events.broadcast(
            HookEvent.TASK_COMPLETED,
            {**body, "status": outcome, "result": getattr(proc.last_result, "message", None)},
            ctx=proc.ctx,
        )
        await events.broadcast(
            HookEvent.SUBAGENT_STOP if child else HookEvent.SESSION_END,
            {**body, "status": outcome},
            ctx=proc.ctx,
        )

    async def _reap_children(self, proc: Process) -> None:
        """Kill whatever this process dispatched. Nobody is left to collect it."""
        for child in self.children(proc):
            if child.alive:
                logger.info(
                    f"| 🧹 [{proc.name}:{proc.pid[:8]}] reaping child "
                    f"{child.name}:{child.pid[:8]}"
                )
                await self.stop(child, force=True, reason="parent exited")

    @staticmethod
    def _final_text(proc: Process, reason: str) -> str:
        """What the parent is told when a child ends."""
        result = proc.last_result
        body = getattr(result, "message", None) or getattr(result, "result", None)
        if body is None and result is not None:
            body = str(result)
        head = f"{proc.name} finished with status {proc.exit_status.value}"
        return "\n".join(part for part in (head, reason or None, body) if part)

    # ==================================================================
    # Internals
    # ==================================================================

    def _resolve(self, target: Optional[Target], *, required: bool = True) -> Any:
        """Accept a Process or a pid; return the live Process."""
        if isinstance(target, Process):
            return target
        proc = self._procs.get(str(target or ""))
        if proc is None and required:
            raise ProcessNotFound(f"no process with pid {target!r}")
        return proc

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        live = sum(1 for proc in self._procs.values() if proc.alive)
        return f"Kernel(processes={len(self._procs)}, live={live})"


#: Process-wide kernel. One per interpreter, like a process table.
kernel = Kernel()


__all__ = ["KILL_GRACE_SECONDS", "Kernel", "kernel"]
