"""ProtocolManager — the channels agents talk over, built on the runtime's transport verbs.

runtime = how messages move (send / ask / suspend-resume / publish); protocol = the SHAPE
of each conversation. One manager, grouped by channel; each channel is one of four patterns:

  gate  (suspend/resume) : escalation — a blocked sub-agent asks its parent, resumes on reply
  ask   (invoke / ask)   : delegation — run another agent and get its Response (meta→sub, meta1→meta2)
                           query      — ask a running agent for a status snapshot
  tell  (send)           : progress   — stream status to the parent
                           control    — cancel / pause / resume a running agent
  fan-out (publish)      : pubsub     — broadcast an event to a topic's subscribers

Add a new channel here only when a real interaction needs it — the runtime primitives are
general; this file is just their typed conversations.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from agentevolver.logger import logger
from agentevolver.protocol.types import (
    ControlMessage,
    EscalationMessage,
    MonitorProgressMessage,
    QueryMessage,
    SubscriptionEventMessage,
)
from agentevolver.response.types import Response
from agentevolver.runtime import runtime_manager
from agentevolver.utils import Singleton, make_id

_ESCALATION_TIMEOUT_S = 300.0

#: Context keys describing *where execution happens*, as opposed to what the
#: agent was asked to do. A sub-agent runs in the same place as its parent, so
#: these carry across a delegation; ``target_name``, the allowlists and the
#: lineage ids are per-delegation and deliberately excluded.
#:
#: The roots matter because a child that does not inherit them is told different
#: paths than its parent while working in the same place, and then goes looking for
#: files where they are not. Seen on ProgramBench, when the execution environment
#: itself failed to carry across: the sub-agent ran `find /` over a whole filesystem
#: hunting for task files that existed elsewhere.
_AMBIENT_CONTEXT_KEYS = (
    "project_root",
    "workspace_root",
    "log_root",
    "extension_root",
    "package_root",
    "shared_extension_root",
    # Internal per-child cwd. Unlike the six layout roots this is never accepted from
    # model input and cannot widen the session; the dispatcher sets it to a disposable
    # worktree and the sandbox adds only that exact path.
    "execution_cwd",
    # Dispatcher-minted per-invocation scheduling/model contract. These are deliberately
    # ambient rather than user-controlled roots: child_brief creates a fresh context and
    # copies only this allowlisted metadata into it.
    "task_contract",
    "child_reasoning_effort",
    # Host-minted ownership metadata. Descendants need the project id for the Gateway's
    # human control surface and the source workspace for stable project-memory identity.
    "project_id",
    "source_workspace",
    "owner",
)


def _inherited_ambient(parent_ctx: Any) -> Dict[str, Any]:
    """The parent's execution environment, for seeding a sub-agent's context."""
    parent_extra = getattr(parent_ctx, "extra", None) or {}
    return {k: parent_extra[k] for k in _AMBIENT_CONTEXT_KEYS if k in parent_extra}


class ProtocolManager(metaclass=Singleton):
    """One handle for every agent-to-agent channel (mirror of ``runtime_manager``)."""

    # ------------------------------------------------------------------
    # escalation — gate: a blocked sub-agent asks its parent (runtime.suspend/resume)
    # ------------------------------------------------------------------

    async def escalate(self, ctx: Any, *, reason: str, situation: str = "", suggestion: str = "") -> str:
        """Send end: post the escalation to the parent and block until it replies. Returns
        the guidance (or a graceful-stop instruction if there is no parent / it times out)."""
        # Lineage lives on the field for an AgentContext, or in ``extra`` once the context
        # has been converted (e.g. into a tool's ToolContext) — check both.
        extra = getattr(ctx, "extra", {}) or {}
        parent_session_id = getattr(ctx, "parent_session_id", None) or extra.get("parent_session_id")
        if not parent_session_id:
            return "No parent to escalate to (running standalone). Proceed on your own or stop gracefully."
        parent_ref = runtime_manager.get(parent_session_id)
        if parent_ref is None:
            return "Parent is no longer running. Proceed on your own or stop gracefully."

        task_id = getattr(ctx, "subtask_id", None) or extra.get("subtask_id") or getattr(ctx, "id", "")
        msg = EscalationMessage(
            task_id=task_id, agent_name=getattr(ctx, "name", "") or "", session_id=getattr(ctx, "id", "") or "",
            reason=reason, situation=situation, suggestion=suggestion,
        )
        logger.info(f"| 🆘 Escalation from {msg.agent_name} [{task_id}] → {parent_session_id}")
        try:
            await runtime_manager.send(parent_ref, msg)
            guidance = await runtime_manager.suspend(task_id, timeout=_ESCALATION_TIMEOUT_S)
        except Exception as e:
            logger.warning(f"| ⏰ Escalation [{task_id}] failed/timeout: {e}")
            return "Parent did not respond in time. Please stop the current subtask gracefully."
        logger.info(f"| 💬 Escalation reply for [{task_id}]: {guidance}")
        return guidance

    def reply(self, task_id: str, guidance: str) -> bool:
        """Reply end: resume the sub-agent suspended on ``task_id``. Returns whether one waited."""
        delivered = runtime_manager.resume(task_id, guidance)
        logger.info(f"| 💬 Reply → [{task_id}]" + ("" if delivered else " (no waiter)"))
        return delivered

    # ------------------------------------------------------------------
    # delegation — ask: run another agent and get its Response (runtime.invoke)
    # ------------------------------------------------------------------

    def child_brief(
        self, child: Any, task: str, *,
        target_name: Optional[str] = None, target_type: Optional[str] = None,
        allowlists: Optional[Dict[str, Any]] = None,
        parent_ref: Any = None, parent_ctx: Any = None, fork: bool = False,
    ) -> Tuple[str, Any]:
        """The task text and the context one delegation hands its child.

        Split out of :meth:`delegate` because a background delegation needs the context
        *before* the child starts — it has to write the child's job id into it — and
        because everything this decides is a rule that must not exist twice. Which
        ambient keys carry across, which per-delegation ones must not, and where an
        evolution target's allowlists come from are each a one-word mistake away from a
        child working in the wrong place or holding a scope granted to its sibling.
        """
        from agentevolver.agent.types import (
            AgentContext,  # local: agent → protocol.server would cycle at import time
        )

        if target_name:
            task = f"Target capability: {target_name}\n\n{task}"
        ctx = AgentContext(
            id=f"{getattr(child, 'name', 'agent')}-{make_id()}",
            parent_session_id=(parent_ref.name if parent_ref is not None else None),
            extra=_inherited_ambient(parent_ctx),
        )
        # Every descendant in one delegation tree carries the root session identity.
        # It lets siblings address one another by job id without weakening the existing
        # cross-session ownership check in ``RuntimeManager.send_to_child``.
        if parent_ctx is not None:
            parent_extra = getattr(parent_ctx, "extra", None) or {}
            root_session_id = parent_extra.get("root_session_id") or getattr(
                parent_ctx, "id", None,
            )
            if root_session_id:
                ctx.extra["root_session_id"] = str(root_session_id)
        if target_name:
            ctx.extra["target_name"] = target_name
        # Which kind of thing the target is. Beside `target_name` because the two are one
        # fact split in half, and the half that says "tool" is the one that picks the
        # registration hook.
        if target_type:
            ctx.extra["target_type"] = target_type
        # A forked child reads its parent's conversation for context. Recorded as the
        # parent's *trace* session — `parent_session_id` above is the ref name, which
        # routes an escalation and names no log.
        if fork and parent_ctx is not None and getattr(parent_ctx, "id", None):
            ctx.extra["forked_from"] = str(parent_ctx.id)
        effective_allowlists = dict(allowlists or {})
        if hasattr(child, "_required_capability_allowlists"):
            required = child._required_capability_allowlists()
            for key, value in required.items():
                requested = effective_allowlists.get(key)
                if requested is not None and list(requested) != list(value):
                    raise ValueError(
                        f"{getattr(child, 'name', 'child')} requires {key}={list(value)!r}; "
                        f"requested {list(requested)!r}"
                    )
                effective_allowlists[key] = list(value)
        if hasattr(child, "_target_capability_allowlists"):
            for key, value in child._target_capability_allowlists(target_name, target_type).items():
                if effective_allowlists.get(key) is None:
                    effective_allowlists[key] = value
        for key, val in effective_allowlists.items():
            if val is not None:
                ctx.extra[key] = val
        return task, ctx

    async def delegate(
        self, child: Any, task: str, *,
        files: Optional[List[str]] = None, target_name: Optional[str] = None,
        target_type: Optional[str] = None,
        allowlists: Optional[Dict[str, Any]] = None, parent_ref: Any = None,
        workspace_root: Optional[str] = None, parent_ctx: Any = None,
        ctx: Any = None, fork: bool = False,
        on_spawn: Optional[Callable[[Any], None]] = None,
    ) -> Response:
        """Run ``child`` on ``task`` as a sub-agent of the caller and return its Response.
        ``parent_ref`` links the child back for escalation; ``allowlists`` optionally
        restricts its capabilities; ``target_name`` anchors an evolution target;
        ``parent_ctx`` is the dispatching agent's context, whose ambient execution
        environment the child inherits.

        ``ctx`` accepts a context already built by :meth:`child_brief` — the caller that
        needed it early passes back the same one rather than a second, differently
        seeded copy."""
        import os

        if ctx is None:
            task, ctx = self.child_brief(child, task, target_name=target_name,
                                         target_type=target_type,
                                         allowlists=allowlists, parent_ref=parent_ref,
                                         parent_ctx=parent_ctx, fork=fork)
        existing = [f for f in (files or []) if os.path.exists(f)]
        return await runtime_manager.invoke(
            child,
            task=task,
            files=existing or None,
            ctx=ctx,
            parent_ref=parent_ref,
            on_spawn=on_spawn,
        )

    # ------------------------------------------------------------------
    # progress — tell: stream status to the parent (runtime.send)
    # ------------------------------------------------------------------

    async def report(self, parent_ref: Any, msg: MonitorProgressMessage) -> None:
        """Post a progress update to the parent's inbox (fire-and-forget)."""
        if parent_ref is not None:
            await runtime_manager.send(parent_ref, msg)

    # ------------------------------------------------------------------
    # control — tell: cancel / pause / resume a running agent (runtime.send)
    # ------------------------------------------------------------------

    async def cancel(self, ref: Any, reason: str = "") -> None:
        """Ask a running agent to stop gracefully (concludes after its current action)."""
        await runtime_manager.send(ref, ControlMessage(action="cancel", reason=reason))

    async def pause(self, ref: Any) -> None:
        """Ask a running agent to stop advancing after its current round (until resumed)."""
        await runtime_manager.send(ref, ControlMessage(action="pause"))
        if hasattr(ref, "paused"):
            ref.paused = True

    async def resume(self, ref: Any) -> None:
        """Let a paused agent continue."""
        await runtime_manager.send(ref, ControlMessage(action="resume"))
        if hasattr(ref, "paused"):
            ref.paused = False

    # ------------------------------------------------------------------
    # query — ask: request a running agent's status snapshot (runtime.ask)
    # ------------------------------------------------------------------

    async def query(self, ref: Any, *, timeout: Optional[float] = 30.0) -> Dict[str, Any]:
        """Ask a running agent for its current status snapshot (step, done, result-so-far, …)."""
        return await runtime_manager.ask(ref, QueryMessage(), timeout=timeout)

    # ------------------------------------------------------------------
    # pubsub — fan-out: broadcast to a topic's subscribers (runtime.publish)
    # ------------------------------------------------------------------

    _TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    @classmethod
    def scoped_topic(cls, topic: str, ctx: Any) -> str:
        """Return a root-session-scoped topic name.

        A model supplies the stable logical name (for example ``website.releases``);
        the protocol adds the task-tree identity.  Two users can therefore run the same
        workflow concurrently without publishing into one another's subscribers.
        """
        logical = str(topic or "").strip()
        if not cls._TOPIC_RE.fullmatch(logical):
            raise ValueError(
                "topic must be 1-128 characters and contain only letters, digits, "
                "dot, underscore, colon, or hyphen"
            )
        extra = getattr(ctx, "extra", None) or {}
        root = str(extra.get("root_session_id") or getattr(ctx, "id", "") or "").strip()
        if not root:
            raise ValueError("publish/subscribe requires a session identity")
        return f"{root}::{logical}"

    def subscribe(self, topic: str, ref: Any) -> None:
        """Start receiving messages published to ``topic`` in ``ref``'s inbox."""
        runtime_manager.subscribe(topic, ref)

    def unsubscribe(self, topic: str, ref: Any) -> None:
        """Stop receiving ``topic``'s messages."""
        runtime_manager.unsubscribe(topic, ref)

    async def publish(self, topic: str, msg: Any) -> int:
        """Deliver one typed event to every running subscriber of an already-scoped topic.

        ``dict`` remains accepted for compatibility with the earlier transport-only API;
        it is normalized into a generic event instead of being placed raw in an Agent inbox.
        New callers should construct :class:`SubscriptionEventMessage` or use
        :meth:`publish_event` so the event type and publisher are explicit.
        """
        if not isinstance(msg, SubscriptionEventMessage):
            payload = msg if isinstance(msg, dict) else {"value": msg}
            msg = SubscriptionEventMessage.create(
                topic=topic,
                event_type=str(payload.get("event_type") or payload.get("event") or "event"),
                payload=payload,
            )
        return await runtime_manager.publish(topic, msg)

    async def publish_event(
        self,
        topic: str,
        *,
        event_type: str,
        payload: Optional[Dict[str, Any]],
        ctx: Any,
        publisher: str = "",
    ) -> Tuple[int, str, SubscriptionEventMessage]:
        """Scope, type, and publish one event; return count, scoped topic, and envelope."""
        scoped = self.scoped_topic(topic, ctx)
        event = SubscriptionEventMessage.create(
            topic=scoped,
            event_type=event_type,
            payload=payload,
            publisher=publisher,
        )
        sent = await runtime_manager.publish(scoped, event)
        return sent, scoped, event


protocol_manager = ProtocolManager()
