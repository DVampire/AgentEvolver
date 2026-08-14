"""SubagentServer — delegation that the parent does not have to sit through.

Delegation was fire-and-forget-and-block: an orchestrator dispatched a child and waited
for its whole run, spending its own steps on wall-clock it was not using. That is the
same defect `agentevolver/job/` was built for, so a background child is registered as a
`Job` of kind ``agent`` rather than in a second registry — one answer to "what is
running", one set of tools to read and stop it.

What this module adds on top of the job registry is the part a shell command does not
have: a child can be *continuable*. A one-shot child answers once and its ref is stopped;
a continuable child stays alive between turns, keeps its session and its memory, and can
be handed more work with ``send_message_tool``. Turns on one child are serialized here,
by the driver, because two tasks delivered into an agent's inbox back to back start two
runs on the same ref and the first one's result is lost.

The channel back up is the job's output. Whatever the child reports mid-run, whatever it
finally returns, and whatever went wrong all accumulate in one place the parent already
knows how to read. That is the same choice `job/README.md` argues for — a job is
collected, not delivered — and it is why there is no separate transcript here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from agentevolver.job import job_manager
from agentevolver.logger import logger
from agentevolver.subagent.types import ChildState, Subagent
from agentevolver.utils import Singleton


class SubagentServer(metaclass=Singleton):
    """Starts children, keeps the continuable ones talking, and reaps them all."""

    def __init__(self) -> None:
        #: Background children by job id. Finished ones stay, stripped of their live
        #: handles: a parent that asks about a child it stopped needs "it finished" and
        #: not "no such child", which is what a popped record would say.
        self._children: Dict[str, Subagent] = {}

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------

    async def run(self, child: Any, task: str, **brief: Any):
        """Delegate and wait — the foreground path, as it always worked.

        Registered as a job anyway, for two reasons. "What is running" then has one
        answer even while a parent is blocked inside a delegation, and a child always
        has somewhere to report to, so ``report_tool`` does not have to behave
        differently depending on how its parent happened to dispatch it.
        """
        from agentevolver.protocol import protocol_manager

        text, ctx = self._brief(child, task, brief)
        job = self._register(child, task, ctx, brief.get("parent_ctx"), continuable=False)

        # Run through a task rather than awaiting directly, so the job's handle is
        # something `job_kill_tool` can actually signal. Without it the registry would
        # accept a kill, mark the job dead, and leave the child running — the one
        # failure the job registry exists to prevent.
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

        # Read before the result is appended, so this is exactly what the child said
        # on its own initiative. A foreground parent never gets to poll the job, so
        # anything it reported would otherwise be written to a file nobody opens.
        reported = job_manager.output(job.id) or ""
        job_manager.append_output(job.id, f"{getattr(response, 'message', '') or ''}\n")
        job_manager.finish(job.id, exit_code=0 if getattr(response, "success", False) else 1)
        if reported.strip():
            return response.model_copy(update={
                "message": f"{response.message}\n\nWhat it reported along the way:\n{reported.strip()}"
            })
        return response

    async def start(self, child: Any, task: str, *, continuable: bool = False,
                    **brief: Any) -> Subagent:
        """Delegate without waiting. Returns as soon as the child holds its first turn.

        Returning early is the whole point, so nothing here waits for the turn to
        begin: the parent's next decision must not be gated on the child's first model
        call, which is the slowest part of starting one.
        """
        from agentevolver.runtime import runtime_manager
        from agentevolver.runtime.types import TaskMessage

        text, ctx = self._brief(child, task, brief)
        job = self._register(child, task, ctx, brief.get("parent_ctx"), continuable=continuable)
        ref = await runtime_manager.spawn(child)

        sub = Subagent(
            job_id=job.id, agent_name=getattr(child, "name", "agent"), task=task,
            parent_session_id=job.session_id, session_id=str(getattr(ctx, "id", "") or ""),
            continuable=continuable, ref=ref, ctx=ctx,
        )
        self._children[job.id] = sub
        await sub.inbox.put(TaskMessage(task=text, kwargs={
            "ctx": ctx, "files": self._existing(brief.get("files")),
            "parent_ref": brief.get("parent_ref"),
        }))
        # Wait for the driver's first step — not for the turn, which is the whole point
        # of backgrounding, but for the coroutine to be *inside* its try block. A task
        # cancelled before it has ever run is closed without executing, so its cleanup
        # never happens: the ref would stay registered and the child would go on working
        # while the job registry reported it killed. That window is one loop iteration
        # wide and a parent that backgrounds a child and stops it in the same turn lands
        # in it.
        running = asyncio.Event()
        sub.driver = asyncio.get_running_loop().create_task(
            self._drive(sub, running), name=f"subagent-{job.id}")
        job.handle = sub.driver
        job.label = sub.label()
        await running.wait()
        logger.info(f"| 🧑‍🚀 Sub-agent {sub.agent_name} started as {job.id} "
                    f"({'continuable' if continuable else 'one-shot'})")
        return sub

    # ------------------------------------------------------------------
    # Talking to a live child
    # ------------------------------------------------------------------

    async def send(self, job_id: str, message: str, *, session_id: str = "") -> str:
        """Queue one more turn on a continuable child. Raises ``ValueError`` if it cannot.

        Raising rather than returning a flag: a message that was not delivered and one
        that was must not read alike, because the parent's next move — wait for a
        result, or do the work itself — depends on which happened.
        """
        from agentevolver.runtime.types import TaskMessage

        sub = self._children.get(job_id)
        if sub is None:
            known = [s.job_id for s in self.list(session_id) if s.alive]
            raise ValueError(
                f"No background sub-agent {job_id!r}. Live ones: "
                f"{', '.join(known) if known else '(none)'}")
        if session_id and sub.parent_session_id and sub.parent_session_id != session_id:
            raise ValueError(f"{job_id} is not your sub-agent; it belongs to another session.")
        if not sub.continuable:
            raise ValueError(
                f"{job_id} is a one-shot sub-agent: it answers once and ends, so there is "
                f"nothing to continue. Read what it returned with job_output_tool, and "
                f"start a continuable one if you need a worker you can keep talking to.")
        if not sub.alive:
            raise ValueError(
                f"{job_id} has already ended, so the message was NOT delivered. Its output "
                f"is still readable with job_output_tool.")

        # Read before the message is queued, and counting what is already queued as
        # busy: a message sent a moment after another lands behind it, and reporting
        # that one as "starting now" would tell the parent to expect an answer that is
        # two turns away.
        busy = sub.state is ChildState.WORKING or not sub.inbox.empty()
        await sub.inbox.put(TaskMessage(task=message, kwargs={"ctx": sub.ctx}))
        logger.info(f"| 📮 Message queued for sub-agent {job_id}: {message[:60]}")
        return ("It already has work in hand, so this waits its turn — a message cannot "
                "redirect work already underway."
                if busy else "It was idle, so it starts on this now.")

    def report(self, job_id: str, output: str, *, agent_name: str = "") -> bool:
        """Write what a child wants its parent to know into the child's own job output.

        The same place its result lands, deliberately. A parent collecting a child has
        one thing to read, in the order the child said it, whether that is a finding
        halfway through or the answer at the end.
        """
        job = job_manager.get(job_id)
        if job is None:
            return False
        who = f" from {agent_name}" if agent_name else ""
        job_manager.append_output(job_id, f"\n[report{who}]\n{output.strip()}\n")
        logger.info(f"| 📣 Sub-agent report on {job_id}: {output.strip()[:80]}")
        return True

    # ------------------------------------------------------------------
    # Reading and reaping
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Subagent]:
        return self._children.get(job_id)

    def list(self, session_id: str = "") -> List[Subagent]:
        """Background children started by one session; all of them when none is given."""
        return [s for s in self._children.values()
                if not session_id or s.parent_session_id == session_id]

    def forget(self, session_id: str) -> None:
        """Stop and drop every background child a finished session started.

        Called from the run's own teardown alongside jobs, terminals and language
        servers. A background child outlives the step that started it by design and
        must not outlive the run: nothing else would ever stop it, and in a long-lived
        host it would keep calling a model on a task whose answer nobody can collect.
        """
        for sub in [s for s in self._children.values() if s.parent_session_id == session_id]:
            self._stop(sub)
            self._children.pop(sub.job_id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _drive(self, sub: Subagent, running: "asyncio.Event") -> None:
        """Run this child's turns, one at a time, until it is one-shot-done or stopped.

        One coroutine per child, alive for the child's whole life. It is also what
        ``job_kill_tool`` cancels, which is why the ref is stopped from a ``finally``
        here rather than by the killer: the registry signals a handle, and a child whose
        handle dies without stopping its pump is a leak the registry then reports as
        dead.
        """
        from agentevolver.runtime import runtime_manager

        try:
            # Inside the try, so from here on a cancellation unwinds through the
            # teardown below rather than closing an unstarted coroutine.
            running.set()
            while True:
                message = await sub.inbox.get()
                sub.state = ChildState.WORKING
                self._relabel(sub)
                job_manager.append_output(
                    sub.job_id, f"\n--- turn {sub.turns + 1}: {(message.task or '')[:120]} ---\n")
                try:
                    response = await runtime_manager.ask(sub.ref, message)
                except Exception as error:                          # noqa: BLE001
                    # A turn that raised is the child's end, not a turn to retry: the
                    # error is a dead ref or a crashed pump, and both mean nothing is
                    # left to send the next message to. A cancellation is not caught
                    # here — it is a stop, and it belongs to the teardown below.
                    job_manager.append_output(sub.job_id, f"[turn failed] {error}\n")
                    job_manager.finish(sub.job_id, error=str(error))
                    return
                sub.turns += 1
                succeeded = bool(getattr(response, "success", False))
                verdict = "finished" if succeeded else "ended without finishing"
                job_manager.append_output(
                    sub.job_id,
                    f"[turn {sub.turns} {verdict}]\n{getattr(response, 'message', '') or ''}\n")
                if not sub.continuable:
                    job_manager.finish(sub.job_id, exit_code=0 if succeeded else 1)
                    return
                sub.state = ChildState.IDLE
                self._relabel(sub)
        finally:
            sub.state = ChildState.GONE
            self._relabel(sub)
            await self._release(sub)

    async def _release(self, sub: Subagent) -> None:
        """Stop the child's pump and drop the live handles; never raise.

        Reached while the driver is being cancelled, so a raise here would replace the
        reason the child was stopped with an error about stopping it.
        """
        from agentevolver.runtime import runtime_manager

        ref, sub.ref, sub.ctx, sub.driver = sub.ref, None, None, None
        try:
            if ref is not None:
                await runtime_manager.stop(ref, drain=False, reason="sub-agent released")
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not stop sub-agent {sub.job_id}: {error}")
        job_manager.finish(sub.job_id, exit_code=0)

    def _stop(self, sub: Subagent) -> None:
        """Cancel a child's driver. The driver's own teardown does the rest."""
        driver = sub.driver
        if driver is not None and not driver.done():
            driver.cancel()
        elif sub.alive:
            # No driver to cancel — the child never got one, or it already returned.
            sub.state = ChildState.GONE

    @staticmethod
    def _brief(child: Any, task: str, brief: Dict[str, Any]):
        """The task text and child context for one delegation, built where delegation
        already builds them — ambient inheritance has exactly one implementation."""
        from agentevolver.protocol import protocol_manager

        return protocol_manager.child_brief(
            child, task,
            target_name=brief.get("target_name"), allowlists=brief.get("allowlists"),
            parent_ref=brief.get("parent_ref"), parent_ctx=brief.get("parent_ctx"),
        )

    def _register(self, child: Any, task: str, ctx: Any, parent_ctx: Any,
                  *, continuable: bool):
        """Take one delegation into the job registry, and tell the child where to report."""
        name = getattr(child, "name", "agent")
        job = job_manager.register(
            kind="agent", label=f"{name} · {task[:60]}",
            session_id=str(getattr(parent_ctx, "id", "") or ""),
        )
        # How ``report_tool`` finds the transcript to write into. On the context rather
        # than passed as an argument, because the tool is called by the child's model
        # and the model must not have to know — or be able to invent — an id.
        if getattr(ctx, "extra", None) is not None:
            ctx.extra["report_job_id"] = job.id
            ctx.extra["report_agent_name"] = name
        return job

    def _relabel(self, sub: Subagent) -> None:
        job = job_manager.get(sub.job_id)
        if job is not None:
            job.label = sub.label()

    @staticmethod
    def _existing(files: Optional[List[str]]) -> Optional[List[str]]:
        import os

        kept = [f for f in (files or []) if os.path.exists(f)]
        return kept or None


subagent_manager = SubagentServer()

__all__ = ["SubagentServer", "subagent_manager"]
