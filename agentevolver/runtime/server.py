"""RuntimeManager — spawn / send / ask / stop / invoke / list / shutdown.

The runtime manages **running** agent refs via a single registry:
    _refs: Dict[str, AgentRef]

Every agent gets one inbox (AgentRef._inbox).  Messages to any running agent
are routed by ref name.  The protocol layer looks up a parent's ref by
parent_session_id (= ref.name) — no separate session registry is needed.
This module also provides the general transport verbs protocols build on:
send / ask / suspend-resume (rendezvous) / publish-subscribe (fan-out).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from agentevolver.logger import logger
from agentevolver.utils import Singleton, make_id
from agentevolver.runtime.pump import _pump
from agentevolver.runtime.types import (
    AgentDeadError,
    AgentRef,
    AgentStatus,
    BaseMessage,
    StopMessage,
    TaskMessage,
)

if TYPE_CHECKING:
    from agentevolver.agent.types import Agent


class RuntimeManager(metaclass=Singleton):
    """Singleton holding all running AgentRefs."""

    def __init__(self) -> None:
        self._refs: Dict[str, AgentRef] = {}
        # Suspend/resume rendezvous channel: key → future. One coroutine suspends on a
        # key and blocks; another (elsewhere, e.g. a different agent) resumes it by key.
        self._pending: Dict[str, "asyncio.Future"] = {}
        # Pub-sub: topic → set of subscriber ref names. Fan-out publish delivers to each.
        self._topics: Dict[str, Set[str]] = {}
        # Background children by job id. Finished ones stay, stripped of their live
        # handles: a parent that asks about a child it stopped needs "it finished" and not
        # "no such child", which is what a record dropped on stop would say. `_refs` holds
        # the running ones and drops them on stop, so the two cannot be merged.
        self._delegated: Dict[str, AgentRef] = {}

    # ------------------------------------------------------------------
    # Suspend / resume channel — a request-reply rendezvous across agents
    # ------------------------------------------------------------------
    # A general pause/resume primitive (think an HTTP request awaiting its response, or a
    # process blocked until signalled). The escalation protocol is one user: a blocked
    # sub-agent ``suspend``s on its task_id; its parent ``resume``s that key with guidance.

    async def suspend(self, key: str, *, timeout: Optional[float] = None) -> Any:
        """Block the caller until ``resume(key, value)`` is called (or timeout), and
        return that value. Registers a one-shot future under ``key``."""
        existing = self._pending.get(key)
        if existing is not None and not existing.done():
            raise ValueError(f"Suspend key collision: {key!r} already has a waiter")
        fut = asyncio.get_running_loop().create_future()
        self._pending[key] = fut
        try:
            if timeout is not None:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            return await fut
        finally:
            self._pending.pop(key, None)

    def resume(self, key: str, value: Any) -> bool:
        """Resume whoever is suspended on ``key`` with ``value``. Returns whether someone
        was actually waiting (False = already resumed / timed out / never suspended)."""
        fut = self._pending.get(key)
        if fut is not None and not fut.done():
            fut.set_result(value)
            return True
        return False

    # ------------------------------------------------------------------
    # Pub-sub — fan-out a message to every running subscriber of a topic
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, ref: AgentRef) -> None:
        self._topics.setdefault(topic, set()).add(ref.name)

    def unsubscribe(self, topic: str, ref: AgentRef) -> None:
        subs = self._topics.get(topic)
        if subs:
            subs.discard(ref.name)
            if not subs:
                self._topics.pop(topic, None)

    async def publish(self, topic: str, msg: BaseMessage) -> int:
        """Deliver ``msg`` to every RUNNING subscriber of ``topic``. Returns the count sent
        (dropping any subscriber that is no longer running)."""
        sent = 0
        for name in list(self._topics.get(topic, set())):
            ref = self._refs.get(name)
            if ref is not None and ref.status == AgentStatus.RUNNING:
                await ref._inbox.put(msg)
                sent += 1
            else:
                self._topics[topic].discard(name)
        return sent

    # ------------------------------------------------------------------
    # Spawn / stop lifecycle
    # ------------------------------------------------------------------

    async def spawn(
        self,
        agent: "Agent",
        *,
        name: Optional[str] = None,
    ) -> AgentRef:
        """Start a pump for one agent instance and register the ref."""
        agent_name = getattr(agent, "name", agent.__class__.__name__)
        ref_name   = name or f"{agent_name}-{make_id()}"
        existing   = self._refs.get(ref_name)
        if existing is not None and existing.status == AgentStatus.RUNNING:
            raise ValueError(f"AgentRef name collision: {ref_name!r} is already RUNNING")

        ref = AgentRef(name=ref_name, agent_name=agent_name, status=AgentStatus.RUNNING)
        ref._pump_task = asyncio.create_task(_pump(agent, ref), name=f"pump-{ref_name}")

        self._refs[ref_name] = ref
        logger.info(f"| 🟢 Runtime spawned: {ref}")
        return ref

    async def stop(
        self,
        ref: AgentRef,
        *,
        drain: bool = True,
        timeout: Optional[float] = None,
        reason: str = "manual",
    ) -> None:
        """Stop the ref's pump."""
        if ref.status != AgentStatus.RUNNING:
            self._refs.pop(ref.name, None)
            return

        ref.status = AgentStatus.STOPPING
        try:
            if drain:
                await ref._inbox.put(StopMessage(reason=reason))
                if ref._pump_task is not None:
                    if timeout is not None:
                        try:
                            await asyncio.wait_for(asyncio.shield(ref._pump_task), timeout=timeout)
                        except asyncio.TimeoutError:
                            logger.warning(f"| ⏱ stop(drain) timeout: {ref.name} — cancelling pump")
                            ref._pump_task.cancel()
                            await asyncio.gather(ref._pump_task, return_exceptions=True)
                    else:
                        await ref._pump_task
            else:
                if ref._pump_task is not None and not ref._pump_task.done():
                    ref._pump_task.cancel()
                    await asyncio.gather(ref._pump_task, return_exceptions=True)
        finally:
            if ref.status != AgentStatus.DEAD:
                ref.status = AgentStatus.STOPPED
            self._refs.pop(ref.name, None)
            logger.info(f"| ⚫ Runtime stopped: {ref}")

    async def shutdown(self) -> None:
        """Stop every running ref."""
        for ref in list(self._refs.values()):
            await self.stop(ref, drain=False, reason="shutdown")

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(self, ref: AgentRef, msg: BaseMessage) -> None:
        """Fire-and-forget into a ref's inbox."""
        if ref.status != AgentStatus.RUNNING:
            raise AgentDeadError(f"Cannot send to {ref}: not RUNNING")
        await ref._inbox.put(msg)

    async def ask(
        self,
        ref: AgentRef,
        msg: BaseMessage,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send into a ref's inbox and await the reply."""
        if msg.reply_future is None:
            msg.reply_future = asyncio.get_running_loop().create_future()
        await self.send(ref, msg)
        if timeout is not None:
            # A caller timeout must not cancel the future owned by the agent. The
            # in-flight handler may still finish and set its result; cancelling the
            # shared future here would otherwise turn that normal completion into an
            # InvalidStateError and kill the long-lived pump.
            return await asyncio.wait_for(asyncio.shield(msg.reply_future), timeout=timeout)
        return await msg.reply_future

    async def invoke(
        self,
        agent: "Agent",
        *,
        name: Optional[str] = None,
        timeout: Optional[float] = None,
        **task_kwargs: Any,
    ) -> Any:
        """One-shot: spawn + ask(TaskMessage) + stop.  Returns agent's result."""
        ref = await self.spawn(agent, name=name)
        try:
            task = task_kwargs.pop("task", None)
            msg  = TaskMessage(task=task, kwargs=task_kwargs)
            return await self.ask(ref, msg, timeout=timeout)
        finally:
            await self.stop(ref, drain=False)

    # ------------------------------------------------------------------
    # Delegation — running one agent on behalf of another
    # ------------------------------------------------------------------
    # Delegating is spawning with two extra obligations: someone is waiting to collect the
    # result, and someone may want to stop it. Both are what `agentevolver/job/` already
    # provides, so a delegated child is registered as a `Job` of kind ``agent`` — one
    # answer to "what is running", one set of tools to read and stop it.
    #
    # Blocking or not is the caller's choice at the call site, not a property of the child.
    # ``delegate`` waits; ``delegate_background`` returns a job id as soon as the child
    # holds its brief. What the child inherits and who it escalates to are identical
    # either way.

    async def delegate(self, child: "Agent", task: str, **brief: Any) -> Any:
        """Delegate and wait — the caller spends its own step on the child's whole run.

        Registered as a job anyway, for two reasons. "What is running" then has one answer
        even while a parent is blocked inside a delegation, and a child always has
        somewhere to report to, so ``report_tool`` does not have to behave differently
        depending on how its parent happened to dispatch it.
        """
        from agentevolver.job import job_manager
        from agentevolver.protocol import protocol_manager

        text, ctx = self._brief(child, task, brief)
        job = self._register(child, task, ctx, brief.get("parent_ctx"))

        # Run through a task rather than awaiting directly, so the job's handle is
        # something `job__kill` can actually signal. Without it the registry would
        # accept a kill, mark the job dead, and leave the child running — the one failure
        # the job registry exists to prevent.
        inner = asyncio.ensure_future(protocol_manager.delegate(
            child, text, files=brief.get("files"),
            parent_ref=brief.get("parent_ref"), ctx=ctx,
        ))
        job.handle = inner
        try:
            response = await inner
        except asyncio.CancelledError:
            job_manager.finish(job.id, error="stopped before it finished")
            raise
        except Exception as error:                                  # noqa: BLE001
            job_manager.finish(job.id, error=str(error))
            raise

        # Read before the result is appended, so this is exactly what the child said on
        # its own initiative. A blocked parent never gets to poll the job, so anything it
        # reported would otherwise be written to a file nobody opens.
        reported = job_manager.output(job.id) or ""
        job_manager.append_output(job.id, f"{getattr(response, 'message', '') or ''}\n")
        job_manager.finish(job.id, exit_code=0 if getattr(response, "success", False) else 1)
        if reported.strip():
            return response.model_copy(update={
                "message": f"{response.message}\n\nWhat it reported along the way:\n{reported.strip()}"
            })
        return response

    async def delegate_background(self, child: "Agent", task: str, *,
                                  continuable: bool = False, **brief: Any) -> Any:
        """Delegate without waiting. Returns as soon as the child holds its first turn.

        Returning early is the whole point, so nothing here waits for the turn to begin:
        the caller's next decision must not be gated on the child's first model call,
        which is the slowest part of starting one.
        """
        from agentevolver.job import job_manager
        from agentevolver.response import Response, ResponseType

        text, ctx = self._brief(child, task, brief)
        job = self._register(child, task, ctx, brief.get("parent_ctx"))

        ref = await self.spawn(child)
        ref.job_id, ref.task = job.id, task
        ref.parent_session_id = job.session_id
        ref.session_id = str(getattr(ctx, "id", "") or "")
        ref.continuable, ref.busy = continuable, True
        ref._ctx = ctx
        self._delegated[job.id] = ref

        await ref._tasks.put(TaskMessage(task=text, kwargs={
            "ctx": ctx, "files": self._existing(brief.get("files")),
            "parent_ref": brief.get("parent_ref"),
        }))
        # Wait for the driver's first step — not for the turn, which is the whole point of
        # backgrounding, but for the coroutine to be *inside* its try block. A task
        # cancelled before it has ever run is closed without executing, so its cleanup
        # never happens: the ref would stay registered and the child would go on working
        # while the job registry reported it killed. That window is one loop iteration
        # wide and a parent that backgrounds a child and stops it in the same turn lands
        # in it.
        running = asyncio.Event()
        ref._driver = asyncio.get_running_loop().create_task(
            self._drive(ref, running), name=f"delegated-{job.id}")
        job.handle, job.label = ref._driver, ref.label()
        await running.wait()
        logger.info(f"| 🧑‍🚀 Delegated {ref.agent_name} in the background as {job.id} "
                    f"({'continuable' if continuable else 'one-shot'})")

        return Response(type=ResponseType.AGENT, success=True,
                        message=self._handoff(ref), data={"job_id": job.id})

    @staticmethod
    def _handoff(ref: AgentRef) -> str:
        """What the caller is told at the moment it backgrounds a child.

        Names the follow-up calls explicitly. A parent handed only an id has to already
        know that a child is a job, and one that does not know goes back to blocking
        delegations — the capability then exists and is never used.
        """
        lines = [
            f"Started {ref.agent_name} in the background as {ref.job_id}. "
            f"It does not block you; keep working.",
            f'  job__output(job_id="{ref.job_id}")  — what it has reported and returned',
            f"  job__list()                      — every job and its state",
            f'  job__kill(job_id="{ref.job_id}")    — stop it',
        ]
        if ref.continuable:
            lines.insert(1, f'  send_message_tool(job_id="{ref.job_id}", message=...) — give it more '
                            f"work on the same conversation")
            lines.append("It stays alive between turns, so collect its result before you finish.")
        else:
            lines.append("It answers once and ends. Collect the result before you finish; "
                         "an uncollected sub-agent is work you paid for and threw away.")
        return "\n".join(lines)

    async def send_to_child(self, job_id: str, message: str, *, session_id: str = "") -> Any:
        """Queue one more turn on a continuable background child.

        A message that was not delivered and one that was must not read alike, because the
        caller's next move — wait for a result, or do the work itself — depends on which
        happened. Undelivered comes back as an unsuccessful ``Response``.
        """
        from agentevolver.response import Response, ResponseType

        def refused(text: str) -> Any:
            return Response(type=ResponseType.AGENT, success=False, message=text)

        ref = self._delegated.get(job_id)
        if ref is None:
            known = [r.job_id for r in self.children(session_id) if r.alive]
            return refused(f"No background sub-agent {job_id!r}. Live ones: "
                           f"{', '.join(known) if known else '(none)'}")
        if session_id and ref.parent_session_id and ref.parent_session_id != session_id:
            return refused(f"{job_id} is not your sub-agent; it belongs to another session.")
        if not ref.continuable:
            return refused(
                f"{job_id} is a one-shot sub-agent: it answers once and ends, so there is "
                f"nothing to continue. Read what it returned with job__output, and "
                f"start a continuable one if you need a worker you can keep talking to.")
        if not ref.alive:
            return refused(
                f"{job_id} has already ended, so the message was NOT delivered. Its output "
                f"is still readable with job__output.")

        # Read before the message is queued, and counting what is already queued as busy:
        # a message sent a moment after another lands behind it, and reporting that one as
        # "starting now" would tell the caller to expect an answer two turns away.
        waiting = ref.busy or not ref._tasks.empty()
        await ref._tasks.put(TaskMessage(task=message, kwargs={"ctx": ref._ctx}))
        logger.info(f"| 📮 Message queued for sub-agent {job_id}: {message[:60]}")
        when = ("It already has work in hand, so this waits its turn — a message cannot "
                "redirect work already underway." if waiting else
                "It was idle, so it starts on this now.")
        return Response(
            type=ResponseType.AGENT, success=True,
            message=(f"Delivered to {job_id}. {when}\nIt does not answer here — read the "
                     f'result with job__output(job_id="{job_id}").'),
            data={"job_id": job_id},
        )

    def child(self, job_id: str) -> Optional[AgentRef]:
        """The ref delegated under ``job_id``, finished ones included."""
        return self._delegated.get(job_id)

    def children(self, session_id: str = "") -> List[AgentRef]:
        """Background children delegated by one session; all of them when none is given."""
        return [r for r in self._delegated.values()
                if not session_id or r.parent_session_id == session_id]

    def forget(self, session_id: str) -> None:
        """Stop and drop every background child a finished session delegated.

        Called from the run's own teardown alongside jobs and terminals.
        A background child outlives the step that started it by design and must not
        outlive the run: nothing else would ever stop it, and in a long-lived host it
        would keep calling a model on a task whose answer nobody can collect.
        """
        for ref in self.children(session_id):
            driver = ref._driver
            if driver is not None and not driver.done():
                driver.cancel()          # its own teardown does the rest
            elif ref.alive:
                # No driver to cancel — it never got one, or it already returned.
                ref.status = AgentStatus.STOPPED
                self._refs.pop(ref.name, None)
            self._delegated.pop(ref.job_id, None)

    async def _drive(self, ref: AgentRef, running: "asyncio.Event") -> None:
        """Run one child's turns, one at a time, until it is one-shot-done or stopped.

        One coroutine per child, alive for the child's whole life. It is also what
        ``job__kill`` cancels, which is why the pump is stopped from a ``finally`` here
        rather than by the killer: the registry signals a handle, and a child whose handle
        dies without stopping its pump is a leak the registry then reports as dead.
        """
        from agentevolver.job import job_manager

        try:
            # Inside the try, so from here on a cancellation unwinds through the teardown
            # below rather than closing an unstarted coroutine.
            running.set()
            while True:
                message = await ref._tasks.get()
                ref.busy = True
                self._relabel(ref)
                job_manager.append_output(
                    ref.job_id, f"\n--- turn {ref.turns + 1}: {(message.task or '')[:120]} ---\n")
                try:
                    response = await self.ask(ref, message)
                except Exception as error:                          # noqa: BLE001
                    # A turn that raised is the child's end, not a turn to retry: the error
                    # is a dead ref or a crashed pump, and both mean nothing is left to
                    # send the next message to. A cancellation is not caught here — it is a
                    # stop, and it belongs to the teardown below.
                    job_manager.append_output(ref.job_id, f"[turn failed] {error}\n")
                    job_manager.finish(ref.job_id, error=str(error))
                    return
                ref.turns += 1
                succeeded = bool(getattr(response, "success", False))
                verdict = "finished" if succeeded else "ended without finishing"
                job_manager.append_output(
                    ref.job_id,
                    f"[turn {ref.turns} {verdict}]\n{getattr(response, 'message', '') or ''}\n")
                if not ref.continuable:
                    job_manager.finish(ref.job_id, exit_code=0 if succeeded else 1)
                    return
                ref.busy = False
                self._relabel(ref)
        finally:
            ref.busy = False
            await self._release(ref)
            self._relabel(ref)

    async def _release(self, ref: AgentRef) -> None:
        """Stop the child's pump and drop the live handles; never raise.

        Reached while the driver is being cancelled, so a raise here would replace the
        reason the child was stopped with an error about stopping it.
        """
        from agentevolver.job import job_manager

        ref._ctx, ref._driver = None, None
        try:
            await self.stop(ref, drain=False, reason="sub-agent released")
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not stop sub-agent {ref.job_id}: {error}")
        job_manager.finish(ref.job_id, exit_code=0)

    @staticmethod
    def _brief(child: "Agent", task: str, brief: Dict[str, Any]):
        """The task text and child context for one delegation, built where delegation
        already builds them — ambient inheritance has exactly one implementation."""
        from agentevolver.protocol import protocol_manager

        return protocol_manager.child_brief(
            child, task,
            target_name=brief.get("target_name"), target_type=brief.get("target_type"),
            allowlists=brief.get("allowlists"),
            parent_ref=brief.get("parent_ref"), parent_ctx=brief.get("parent_ctx"),
            fork=bool(brief.get("fork")),
        )

    @staticmethod
    def _register(child: "Agent", task: str, ctx: Any, parent_ctx: Any):
        """Take one delegation into the job registry, and tell the child where to report."""
        from agentevolver.job import job_manager

        name = getattr(child, "name", "agent")
        job = job_manager.register(
            type="agent", label=f"{name} · {task[:60]}",
            session_id=str(getattr(parent_ctx, "id", "") or ""),
        )
        # How ``report_tool`` finds the transcript to write into. On the context rather
        # than passed as an argument, because the tool is called by the child's model and
        # the model must not have to know — or be able to invent — an id.
        if getattr(ctx, "extra", None) is not None:
            ctx.extra["report_job_id"] = job.id
            ctx.extra["report_agent_name"] = name
        return job

    @staticmethod
    def _relabel(ref: AgentRef) -> None:
        from agentevolver.job import job_manager

        job = job_manager.get(ref.job_id)
        if job is not None:
            job.label = ref.label()

    @staticmethod
    def _existing(files: Optional[List[str]]) -> Optional[List[str]]:
        import os

        kept = [f for f in (files or []) if os.path.exists(f)]
        return kept or None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[AgentRef]:
        return self._refs.get(name)

    def list(self) -> List[AgentRef]:
        return list(self._refs.values())


runtime_manager = RuntimeManager()
