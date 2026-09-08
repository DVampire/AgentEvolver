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


#: Where a context records which allowlists were granted rather than defaulted. A grant
#: survives a step; a default is re-derived from the agent's class field each time.
GRANTED_ALLOWLISTS = "_granted_allowlists"


def grant(extra: Dict[str, Any], key: str) -> None:
    """Mark one allowlist in this context as granted rather than defaulted.

    Without the mark the next step overwrites it from the agent's class field, because
    that is how a default stays current. One function so the dispatch path and any
    later grant record it the same way.
    """
    marked = list(extra.get(GRANTED_ALLOWLISTS) or ())
    if key not in marked:
        marked.append(key)
    extra[GRANTED_ALLOWLISTS] = marked


def child_context(child: Any, brief: Dict[str, Any], parent: Any, ctx: Any) -> Any:
    """A context of the child's own, carrying only what a child should inherit.

    Not the parent's context. Sharing it would give a child the parent's session id,
    and with it the parent's memory and budgets — so two agents would be writing one
    history. What crosses is lineage, the resource and acceptance contract, and the
    files: the child then knows what it is scoped to and what it will be judged
    against, rather than learning both from a paraphrase in its task.

    The one place a child's context is built, for every caller that has to build one —
    including the ones that must build it before a loop exists to dispatch from. The
    website builder had a second copy for exactly that reason, and the two drifted:
    this one grants no capability allowlists to a subscriber and never set
    ``root_session_id``, that one carried neither the task contract nor the dispatch
    scoping. Removing a key from context inheritance meant finding both.
    """
    from agentevolver.agent.types import AgentContext

    contract = {
        key: brief[key]
        for key in ("read_set", "write_set", "acceptance", "owner")
        if brief.get(key)
    }
    inherited = dict(getattr(ctx, "extra", None) or {})
    # Scoping the parent chose for this dispatch travels; the parent's own run state
    # does not.
    # `trace_integrity_profile` was inherited here too. It is configuration, read from
    # `config` where it is declared and validated, so passing it down a dispatch chain
    # gave a run's descendants a second place to disagree with the setting.
    keep = {"plugin_allowlist", "workflow_allowlist", "source_workspace",
            # Topics are namespaced `{root}::{name}`, so a subscriber that resolves a
            # different root than its publisher subscribes to a string nobody sends
            # to. A child always has its own session id, which makes every dispatched
            # subscriber silent unless the root travels — `subscription_topics` is in
            # the dispatch schema precisely so an agent needs no code of its own, and
            # without this it needed code of its own to work at all.
            "root_session_id"}
    extra = {key: value for key, value in inherited.items() if key in keep}
    extra.setdefault("root_session_id", str(getattr(ctx, "id", "") or ""))
    # History sharing is a grant for this dispatch, never inherited transitively.
    extra["fork"] = brief.get("fork") is True
    if "reasoning_effort" in brief:
        extra["child_reasoning_effort"] = brief["reasoning_effort"]
    # What the parent chose FOR THIS DISPATCH, as opposed to what it inherited. The
    # evolution roles read their target from the context, because a generate run's
    # target does not exist yet and so cannot be looked up by name. The dispatch
    # schema has always declared these two and nothing carried them across, so every
    # generate run ended `target_type must be one of ...; got ''` — 47 steps and
    # $3.46 in one measured run, registering nothing.
    for key in ("target_type", "target_name"):
        value = str(brief.get(key) or "").strip()
        if value:
            extra[key] = value
    # Capability grants this dispatch makes. The dispatch schema has declared all
    # five for as long as it has existed and nothing read them, so a parent narrowing
    # or widening a child's roster was silently ignored and the child's class default
    # stood — which is how an isolation contract meant to keep a visitor out of the
    # workspace also made it impossible to hand that visitor a newly evolved tool.
    #
    # An empty list is a real grant and means "none of this kind", so presence is
    # what matters here, not truthiness.
    for key in ("tool_allowlist", "skill_allowlist", "connector_allowlist",
                "plugin_allowlist", "workflow_allowlist", "environment_allowlist"):
        if isinstance(brief.get(key), list):
            extra[key] = [str(item).strip() for item in brief[key] if str(item).strip()]
            grant(extra, key)
    extra["task_contract"] = contract
    extra["task_files"] = list(brief.get("files") or ())
    extra["parent_session_id"] = str(getattr(ctx, "id", "") or "")
    return AgentContext(
        # The child's, not the parent's. `ctx.name` is read as "the agent this
        # context belongs to" — it is the `agent_name` a tool execution records, the
        # publisher an event carries, the asker on a question — so naming a child's
        # context after its parent filed the child's every action under the parent.
        name=getattr(child, "name", "") or getattr(parent, "name", ""),
        extra=extra,
        parent_session_id=str(getattr(ctx, "id", "") or ""),
    )




class Kernel:
    """Creates, schedules, connects and reaps agent processes."""

    def __init__(self) -> None:
        self._procs: Dict[str, Process] = {}
        self._topics = TopicRegistry()
        #: When each process was last handed work by `assign`. The round-robin half of
        #: competing consumers: without it an idle pool ties on every other key and one
        #: worker takes everything.
        self._assigned_at: Dict[str, float] = {}
        self._closing = False

    # ==================================================================
    # Process lifecycle
    # ==================================================================

    async def bootstrap_subscribers(
        self,
        subscribers: Sequence[Dict[str, Any]],
        *,
        parent: Any,
        ctx: Any,
    ) -> Dict[str, str]:
        """Register a task's initial subscribers through the normal dispatch path.

        Each declaration contains ``id``, ``agent`` and a dispatch ``brief`` with
        ``subscription_topics``. The caller supplies domain-specific briefs; runtime
        owns validation, creation, retry identity and cleanup. No model turn runs
        until an event is delivered. Repeating the same setup returns the same IDs;
        changing an established setup requires an explicit lifecycle operation.
        """
        import copy
        import hashlib
        import json

        from agentevolver.runtime.modes import for_brief, topics_of

        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            raise ValueError("Subscriber setup requires a context with extra state")
        declarations = []
        identifiers = set()
        for item in subscribers:
            if not isinstance(item, dict):
                raise ValueError("Each subscriber must declare id, agent and brief")
            identifier = str(item.get("id") or "").strip()
            name = str(item.get("agent") or "").strip()
            brief = item.get("brief")
            if not identifier or not name or not isinstance(brief, dict):
                raise ValueError("Each subscriber must declare id, agent and brief")
            if identifier in identifiers:
                raise ValueError(f"Duplicate subscriber id: {identifier}")
            identifiers.add(identifier)
            topics = brief.get("subscription_topics")
            if (
                not isinstance(topics, list)
                or not topics
                or any(not isinstance(topic, str) or not topic.strip() for topic in topics)
                or for_brief(brief) is not InteractionMode.SUBSCRIBER
            ):
                raise ValueError(f"Subscriber {identifier!r} needs subscription_topics")
            check_topics(InteractionMode.SUBSCRIBER, topics_of(brief))
            declarations.append((identifier, name, copy.deepcopy(brief)))

        signature = hashlib.sha256(
            json.dumps(declarations, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        existing = extra.get("_runtime_subscribers")
        if existing is not None:
            if existing.get("signature") != signature:
                raise ValueError("Subscriber setup differs from the established task setup")
            return dict(existing["jobs"])

        created: List[Process] = []
        jobs: Dict[str, str] = {}
        try:
            for identifier, name, brief in declarations:
                proc = await self.dispatch(name, brief, parent=parent, ctx=ctx)
                created.append(proc)
                jobs[identifier] = proc.pid
        except BaseException:
            # A missing later role must not leave a partially registered panel alive.
            for proc in reversed(created):
                await self.stop(proc, force=True, reason="Subscriber setup failed")
            for proc in created:
                await self.wait(proc)
            raise
        extra["_runtime_subscribers"] = {"signature": signature, "jobs": dict(jobs)}
        return jobs

    async def dispatch(
        self, name: str, brief: Dict[str, Any], *, parent: Any, ctx: Any,
    ) -> Process:
        """Start one child agent by name under ``parent``, and return its process.

        The upper half of :meth:`spawn`: that one takes an agent already built and told
        what to be, this one takes the name of a registered agent and a dispatch brief,
        and does everything between. Resolve the template and take a fresh copy of it,
        narrow its permission to the parent's, apply what the brief overrides, read the
        mode out of the brief, build the child's context, isolate a worktree when asked.

        Public, and here, because dispatching is not the model's alone. ``modes`` says a
        subscriber is five facts — resident, idle at start, a topic edge, a standing
        brief, *and a context of its own* — assembled wrongly at every call site until
        something named it. Three of those five ended up here; the last two stayed in the
        agent layer behind a model tool call, so a run needing children before its own
        first step had to assemble them by hand after all, and did: skipping the
        permission narrowing, the mode derivation and the worktree, and drifting on what
        a child inherits. A mode is only one place if the whole of it is in one place.

        Raises:
            LookupError: nothing is registered under ``name``.
            ValueError: the brief contradicts itself, or a worktree cannot be isolated.
        """
        from contextlib import ExitStack

        from agentevolver.agent.server import agent_manager
        from agentevolver.permission import permission_manager
        from agentevolver.runtime.modes import for_brief, topics_of

        template = await agent_manager.get(name)
        if template is None:
            raise LookupError(f"Agent {name!r} is not registered")
        # The registry holds the program; each dispatch is its own process. A template
        # without `fresh` is used as-is, which is what a stub in a test usually is.
        fresh = getattr(template, "fresh", None)
        child = fresh() if callable(fresh) else template

        child.permission_mode = permission_manager.restrict(
            getattr(child, "permission_mode", None), getattr(parent, "permission_mode", None),
            getattr(getattr(parent, "proc", None), "permission_mode", None),
        ).value
        if brief.get("model"):
            child.model_name = str(brief["model"]).strip()
        if "token_budget" in brief and getattr(child, "allow_token_budget_override", True):
            # A delegation can narrow the host's cap, never raise it.
            limit = getattr(child, "max_token", None)
            budget = brief["token_budget"]
            child.max_token = min(limit, budget) if limit is not None else budget
        elif "token_budget" in brief:
            logger.info(
                f"{name} retains configured token budget {child.max_token}; dispatch "
                f"override {brief['token_budget']} is disabled for this role"
            )

        mode, topics = for_brief(brief), topics_of(brief)
        child_ctx = child_context(child, brief, parent, ctx)
        tree = None
        try:
            with ExitStack() as scope:
                if brief.get("isolate_worktree"):
                    tree = await self._isolate_worktree(child, child_ctx, scope)
                return await self.spawn(
                    child, str(brief.get("task") or "").strip(), mode=mode,
                    files=list(brief.get("files") or ()), ctx=child_ctx,
                    parent=getattr(parent, "proc", None), topics=topics,
                    **({"worktree": tree} if tree is not None else {}),
                )
        except BaseException:
            if tree is not None:
                await tree.cleanup()
            raise

    @staticmethod
    async def _isolate_worktree(child: Any, child_ctx: Any, scope: Any) -> Any:
        """A disposable copy of the workspace for one child, entered on ``scope``."""
        import os

        from agentevolver.paths import path_manager
        from agentevolver.permission import permission_manager
        from agentevolver.sandbox.worktree import IsolatedWorktree
        from agentevolver.utils import make_id

        roots = path_manager.session_roots()
        if not roots or os.getenv("AGENTEVOLVER_EXEC_CONTAINER"):
            raise ValueError("Worktree dispatch requires a bound host Git workspace")
        if set(getattr(child, "env_names", ())) - {"job"}:
            raise ValueError("Worktree dispatch does not relocate browser/remote environments")
        source = str(roots["workspace"])
        tree = await IsolatedWorktree.create(source, str(roots["log"]), make_id())
        child_ctx.extra.setdefault("source_workspace", source)
        # The override is the record. `spawn` starts the child with `asyncio.create_task`
        # inside this scope, so the child's context snapshot carries it for the whole of
        # its life even after this stack unwinds.
        scope.enter_context(path_manager.workspace(tree.path))
        scope.enter_context(permission_manager.relocate(source, str(tree.path)))
        return tree

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
        thread_id: str = "",
        resume: bool = False,
        worktree: Any = None,
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
            thread_id: Stable dialogue identity; defaults to the agent context id.
            resume: Explicitly restore a saved Agent dialogue under the same bound
                session and model route. Does not restore external processes or pages.

        Returns:
            The live :class:`Process`. It is already running; nothing needs to be awaited
            unless the caller wants the result.
        """
        if self._closing:
            raise RuntimeError("Kernel is shutting down; no new process may start")
        parent_proc = self._resolve(parent) if parent else None
        if parent_proc is not None and (not parent_proc.alive or parent_proc.signals.terminal):
            raise RuntimeError("Cannot spawn work under a stopping or exited parent")
        if parent_proc is not None:
            parent_proc.budget.check()
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

        from hashlib import sha256

        from agentevolver.paths import P, path_manager

        pid = make_id()
        stable_thread = thread_id or str(getattr(ctx, "id", "") or "")
        if stable_thread and path_manager.session is not None:
            endpoint = path_manager.get(P.SESSION_RUN_STATE, thread_id=stable_thread)
            pid = sha256(str(endpoint).encode()).hexdigest()[:24]
        if pid in self._procs and not self._procs[pid].exited:
            raise RuntimeError("This durable endpoint is already running")
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
            thread_id=thread_id,
            resume=resume,
        )
        from agentevolver.paths import P, path_manager

        restored = False
        if ctx is not None and path_manager.session is not None:
            state_path = path_manager.get(P.SESSION_RUN_STATE, thread_id=proc.thread_id)
            try:
                proc.mailbox.bind(
                    state_path.with_suffix(".mailbox.json"), resume=resume,
                    identity={"thread": proc.thread_id, "name": proc.name,
                              "model": str(getattr(agent, "model_name", "")),
                              "mode": mode.value, "brief": proc.brief},
                    topics=self._scope_all(topics, ctx),
                )
                if parent_proc is None:
                    proc.budget.bind(state_path, resume=resume)
                restored = resume
                if restored:
                    from agentevolver.runtime.process import MAX_REMEMBERED_TURNS

                    turns = proc.mailbox.turns
                    proc.turns = max(turns, default=0)
                    for index in sorted(turns)[-MAX_REMEMBERED_TURNS:]:
                        proc.turn_results[index] = turns[index]["message"]
                        proc.turn_success[index] = turns[index]["success"]
                if restored and not resident:
                    if len(proc.mailbox) and (task or files):
                        raise ValueError("Queued work exists; resume without a new task before sending another")
                    if not len(proc.mailbox) and not (task or files):
                        raise RuntimeError("This one-shot endpoint has no queued work or new task to resume")
            except BaseException:
                proc.mailbox.release()
                raise
        self._procs[pid] = proc
        proc.worktree = worktree
        if restored:
            self._topics.subscribe_many(pid, proc.mailbox.topics)
        elif topics:
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
        from agentevolver.paths import path_manager

        if path_manager.session is not None:
            proc._path_lease = path_manager.lease()
            proc._path_lease.__enter__()
        driver = None
        try:
            if restored and not resident and not len(proc.mailbox):
                # A completed dialogue can continue with an explicitly supplied
                # new assignment. This is not replay of its previous operation.
                proc.mailbox.put(first)
            if not start_idle and not restored:
                proc.record_delivery(first, "queued")
            driver = self._serve(proc, None if start_idle or restored else first)
            proc._task = asyncio.create_task(driver, name=f"proc-{pid[:8]}-{proc.name}")
        except BaseException:
            if driver is not None:
                driver.close()
            proc.mailbox.release()
            self._topics.drop(pid)
            self._procs.pop(pid, None)
            if proc._path_lease is not None:
                proc._path_lease.__exit__(None, None, None)
                proc._path_lease = None
            raise
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
        if proc._cleanup is not None:
            return True  # Cleanup is tracked and must not be cancelled by a second stop.
        signal = Signal.KILL if force else Signal.STOP
        proc.signals.raise_signal(signal, reason)
        logger.info(
            f"| 🛑 [{proc.name}:{proc.pid[:8]}] {signal.value}"
            + (f": {reason}" if reason else "")
        )
        if force and proc._started and proc._task is not None and not proc._task.done():
            proc._task.cancel()
        elif proc._task is None:
            proc.transition(ProcessState.STOPPING)
            proc._cleanup = asyncio.create_task(
                self._exit(proc, ExitStatus.CANCELLED, reason, graceful=not force)
            )
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
            known = proc.mailbox.known(envelope)
            if known is not None:
                return known in {"queued", "received", "delivered"}
            if envelope.id in proc.deliveries:
                return proc.deliveries[envelope.id]["status"] in {"queued", "received", "delivered"}
            proc.mailbox.put(envelope)
            proc.record_delivery(envelope, "queued")
        except MailboxClosed:
            return False
        return True

    async def send_task(self, target: Target, task: str, **kwargs: Any) -> bool:
        """Give a resident process its next turn."""
        return await self.send(target, TaskEnvelope(task=task, kwargs=dict(kwargs)))

    async def reply(self, target: Target, text: str, *, in_reply_to: str = "") -> bool:
        """Unblock a child that is waiting inside :meth:`Process.ask_parent`."""
        proc = self._resolve(target, required=False)
        if proc is None or not proc.waiting_for:
            return False
        question = in_reply_to or proc.waiting_for
        if question != proc.waiting_for:
            return False
        return await self.send(
            proc, ReplyEnvelope(sender=proc.parent_pid, text=text, in_reply_to=question)
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
        scoped_topic = self._scope_all([topic], proc.ctx)[0]
        proc.mailbox.subscribe(scoped_topic)
        return self._topics.subscribe(proc.pid, scoped_topic)

    def unsubscribe(self, target: Target, topic: str) -> bool:
        proc = self._resolve(target)
        scoped_topic = self._scope_all([topic], proc.ctx)[0]
        proc.mailbox.subscribe(scoped_topic, remove=True)
        return self._topics.unsubscribe(proc.pid, scoped_topic)

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

    async def shutdown(self, *, timeout: float = KILL_GRACE_SECONDS) -> List[str]:
        """Request stop, join within the deadline, and retain/report unfinished PIDs.

        A deadline limits the caller's wait, not the lifetime of tracked cleanup.
        Calling shutdown again can collect processes which have since finished.
        """
        self._closing = True
        waiters = []
        try:
            pending = [proc for proc in self._procs.values() if not proc._exited.is_set()]
            for proc in pending:
                await self.stop(proc, force=True, reason="kernel shutdown")
            waiters = [asyncio.create_task(proc._exited.wait()) for proc in pending]
            if waiters:
                await asyncio.wait(waiters, timeout=max(0, timeout))
            self.forget()
            remaining = list(self._procs)
            if remaining:
                logger.warning(f"| ⚠️ shutdown still awaiting cleanup: {remaining}")
            return remaining
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            self._closing = bool(self._procs)

    def forget(self, *, session_id: str = "") -> int:
        """Drop exited processes from the table. Returns how many were removed."""
        gone = [
            pid for pid, proc in self._procs.items()
            if proc._exited.is_set() and (not session_id or proc.session_id == session_id)
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
        proc._started = True
        budget_scope = proc.budget.scope()
        budget_scope.__enter__()
        try:
            if proc.signals.terminal:
                await proc._honor_signals()
            await self._announce(proc, "start", first)
            envelope: Optional[Envelope] = first
            if envelope is None:
                proc.transition(ProcessState.IDLE)
                envelope = await proc.recv()
            else:
                proc.transition(ProcessState.RUNNING)
                proc.record_delivery(envelope, "received")
                await proc._hook("on_start", envelope.task, proc)

            started = first is not None
            while envelope is not None:
                if proc.state is not ProcessState.RUNNING:
                    proc.transition(ProcessState.RUNNING)
                if not started:
                    await proc._hook("on_start", self._input_text(proc, envelope), proc)
                    started = True

                proc.last_result = await self._turn(proc, envelope)
                proc.record_turn(proc.turns + 1, proc.last_result, envelope=envelope)

                if not proc.resident:
                    if not proc.turn_success[proc.turns]:
                        status = ExitStatus.FAILED
                        reason = proc.turn_results[proc.turns] or "Agent returned no successful result"
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
            proc.transition(ProcessState.STOPPING)
            proc._cleanup = asyncio.create_task(
                self._exit(proc, status, reason, graceful=graceful),
                name=f"cleanup-{proc.pid}",
            )
            try:
                await asyncio.shield(proc._cleanup)
            finally:
                budget_scope.__exit__(None, None, None)

    async def _turn(self, proc: Process, envelope: Envelope) -> Any:
        """Run the agent once over one input."""
        proc.budget.check()
        task = self._input_text(proc, envelope)
        files = list(getattr(envelope, "files", ()) or ())
        kwargs = dict(getattr(envelope, "kwargs", {}) or {})
        logger.info(f"| ▶️ [{proc.name}:{proc.pid[:8]}] turn {proc.turns + 1}")
        proc.record_delivery(envelope, "received")
        try:
            from agentevolver.permission import permission_manager

            with permission_manager.scope(proc.permission_mode), proc.budget.scope():
                result = await proc.agent(task=task, files=files, ctx=proc.ctx, **kwargs)
        except (Stopped, Killed, asyncio.CancelledError):
            proc.record_delivery(envelope, "interrupted")
            raise
        except Exception:
            proc.record_delivery(envelope, "failed")
            raise
        return result

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
        """The single exit path: land, unsubscribe, reap, notify, then mark exited.

        Every ending goes through here — finished, failed, stopped, killed — so the
        clean-up and the parent notification exist once instead of once per outcome.
        """
        if proc.exited:
            return
        # This is a tracked cleanup task; repeated stop calls cannot cancel it.
        # Ordinary cleanup errors are logged while later cleanup still runs. Waiters
        # are released only after these steps, not when the driver first stops.
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
            self._topics.drop(proc.pid)
            undelivered = proc.mailbox.close()
            for envelope in undelivered:
                try:
                    proc.record_delivery(envelope, "undelivered")
                except Exception as error:
                    proc.cleanup_errors.append({"phase": "mailbox checkpoint", "error": str(error)})
            proc.signals.clear()
            if undelivered:
                logger.info(
                    f"| 📭 [{proc.name}:{proc.pid[:8]}] exited with {len(undelivered)} "
                    f"undelivered message(s)"
                )

            await self._guarded(self._reap_children(proc), proc, "reaping children")

            await self._guarded(proc._hook("on_exit", status), proc, "on_exit")
            if proc.worktree is not None:
                if proc.cleanup_errors:
                    proc.artifacts["worktree"] = str(proc.worktree.path)
                else:
                    await self._guarded(self._collect_workspace(proc), proc, "collecting worktree")

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

            await self._guarded(
                self._announce(proc, "exit", None, status=status), proc, "exit events"
            )
        finally:
            proc.ended_at = time.time()
            proc.transition(ProcessState.EXITED)
            proc.mailbox.release()
            if proc._path_lease is not None:
                proc._path_lease.__exit__(None, None, None)
                proc._path_lease = None
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
            await awaitable
        except Exception as error:  # noqa: BLE001
            proc.cleanup_errors.append({"phase": what, "error": f"{type(error).__name__}: {error}"})
            logger.warning(f"| ⚠️ [{proc.name}:{proc.pid[:8]}] {what} failed: {error}")

    @staticmethod
    async def _collect_workspace(proc: Process) -> None:
        """Archive a patch before removing a worktree; retain source on any failure."""
        from agentevolver.utils.file_utils import atomic_write_text

        tree = proc.worktree
        proc.artifacts["worktree"] = str(tree.path)
        patch = await tree.collect_patch()
        atomic_write_text(tree.patch_path, patch)
        proc.artifacts["patch"] = str(tree.patch_path)
        await tree.cleanup()
        proc.artifacts.pop("worktree", None)
        proc.worktree = None

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
        body["cleanup_errors"] = [dict(error) for error in proc.cleanup_errors]
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
        children = self.children(proc)
        for child in children:
            if child.alive:
                logger.info(
                    f"| 🧹 [{proc.name}:{proc.pid[:8]}] reaping child "
                    f"{child.name}:{child.pid[:8]}"
                )
                await self.stop(child, force=True, reason="parent exited")
        await asyncio.gather(*(child._exited.wait() for child in children))

    @staticmethod
    def _final_text(proc: Process, reason: str) -> str:
        """What the parent is told when a child ends."""
        result = proc.last_result
        body = getattr(result, "message", None) or getattr(result, "result", None)
        if body is None and result is not None:
            body = str(result)
        head = f"{proc.name} finished with status {proc.exit_status.value}"
        artifacts = "\n".join(f"{name}: {path}" for name, path in proc.artifacts.items())
        return "\n".join(part for part in (head, reason or None, body, artifacts) if part)

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
