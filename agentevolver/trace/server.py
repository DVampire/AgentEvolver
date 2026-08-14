"""TraceManager — singleton facade for the whole trace subsystem.

Lifecycle::

    await trace_manager.initialize(log_root="output/example/log/trace")
    await trace_manager.start()          # starts the writer
    ...
    await trace_manager.emit(event)      # non-blocking async emit
    ...
    await trace_manager.stop()

Trace persists events and fans them out to subscribers; it does not serve a UI
of its own. Consumers render them: the Gateway forwards subscribed events to
the web frontend, and the ``.jsonl`` files under ``<log_root>/trace`` remain
available for offline inspection.
"""

from __future__ import annotations

import inspect
import os
from typing import Optional

from agentevolver.logger import logger
from agentevolver.queue import AsyncQueue
from agentevolver.trace.types import TraceEvent
from agentevolver.trace.writer import TraceWriter
from agentevolver.utils import Singleton


class TraceManager(metaclass=Singleton):
    """Singleton that owns the event queue and writer."""

    def __init__(self) -> None:
        self._log_root: Optional[str] = None
        self._queue: Optional[AsyncQueue[TraceEvent]] = None
        self._writer: Optional[TraceWriter] = None
        self._initialized: bool = False
        self._running: bool = False
        self._subscribers = set()
        #: session_id → next sequence number. Assigned here rather than in the writer
        #: because the writer consumes the queue asynchronously: numbering there would
        #: leave every subscriber holding an event whose position is still unknown.
        self._next_seq: dict[str, int] = {}
        #: session_id → the live surface, in history order. Maintained here because this
        #: is already the one funnel that sees every event in order, and because a
        #: producer that wants to replace a range needs to know what is in it — which it
        #: cannot work out from its own records alone.
        self._surface: dict[str, list[int]] = {}
        #: session_id → the session's events, for consumers that project the log rather
        #: than follow it (see `derive.py`). Held only while a session is live; the
        #: durable copy is the writer's.
        self._events: dict[str, list[TraceEvent]] = {}
        #: Per-session retention cap. Exceeding it drops the session from retention
        #: entirely rather than keeping a suffix: a projection built from a truncated log
        #: silently loses the turns at the front, which reads as a shorter conversation
        #: rather than as a missing one. `events()` then returns nothing, and a caller
        #: that needs the whole log can tell "not retained" from "nothing happened".
        self._max_retained: int = 20_000

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, log_root: Optional[str] = None) -> None:
        """Set log_root and create queue / writer.  Idempotent.

        If log_root is omitted, defaults to ``{config.log_root}/trace``.
        """
        if self._initialized:
            return
        if log_root is None:
            from agentevolver.config import config
            log_root = os.path.join(config.log_root, "trace")
        self._log_root = log_root

        self._queue = AsyncQueue[TraceEvent](maxsize=20_000)
        self._writer = TraceWriter(log_root=log_root, queue=self._queue)
        self._initialized = True
        logger.info(f"| 🔍 TraceManager initialised (log_root={log_root})")

    def rebind(self, log_root: str) -> None:
        """Re-point the trace root at ``<log_root>/trace`` for a newly bound session.

        Long-lived hosts (the Gateway) initialize this manager once, before any
        session exists; binding a session re-points it (and its writer) so each
        session's event files and index live under its own log root.
        """
        trace_root = os.path.join(log_root, "trace")
        self._log_root = trace_root
        if self._writer is not None:
            self._writer.rebind(trace_root)

    async def start(self) -> None:
        """Start the writer consumer loop."""
        if not self._initialized:
            raise RuntimeError("TraceManager.initialize() must be called first")
        if self._running:
            return

        self._writer.start()
        self._running = True

    async def stop(self) -> None:
        """Drain queue and flush the writer."""
        if not self._running:
            return
        self._running = False

        if self._writer:
            await self._writer.stop()

        logger.info("| ⏹️  TraceManager stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def emit(self, event: TraceEvent) -> None:
        """Emit a trace event.  Never blocks on the caller, never raises.

        Stamps the event's position in its session's log before anyone sees it. The
        number is what lets one event cite another — a summary naming the range it
        replaced, a projection naming what the model saw — none of which can be
        expressed by "somewhere earlier in the file".
        """
        if not self._running or self._queue is None:
            return
        session = event.session_id or "no_session"
        if event.seq_no is None:
            event.seq_no = self._claim_seq(session)
        self._apply_surface(session, event)
        self._retain(session, event)
        self._queue.emit(event)
        for subscriber in tuple(self._subscribers):
            try:
                result = subscriber(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️  Trace subscriber failed: {exc}")

    def _claim_seq(self, session_id: str) -> int:
        """The next position in one session's log.

        Seeded from the writer's index the first time a session is seen, so a session
        reopened in a new process continues its numbering instead of restarting at 0 and
        producing two events that claim the same position.
        """
        if session_id not in self._next_seq:
            written = 0
            writer = self._writer
            if writer is not None:
                meta = getattr(writer, "_session_meta", {}).get(session_id) or {}
                written = int(meta.get("event_count") or 0)
            self._next_seq[session_id] = written
        seq = self._next_seq[session_id]
        self._next_seq[session_id] = seq + 1
        return seq

    def _apply_surface(self, session_id: str, event: TraceEvent) -> None:
        """Advance the live surface by one event.

        Deliberately forgiving where :func:`fold_surface` is strict. That function reads
        a stored log and must refuse one it cannot interpret; this one is on the emit
        path, where dropping a *live* event because its declaration looked wrong would
        lose the event itself. A malformed replacement is left as an append.
        """
        op = event.surface_op
        if op is None or event.seq_no is None:
            return
        nodes = self._surface.setdefault(session_id, [])
        if isinstance(op, dict) and op.get("op") == "replace":
            try:
                i, j = nodes.index(int(op["start"])), nodes.index(int(op["end"]))
            except (KeyError, TypeError, ValueError):
                nodes.append(event.seq_no)
                return
            if i <= j:
                nodes[i : j + 1] = [event.seq_no]
                return
        nodes.append(event.seq_no)

    def _retain(self, session_id: str, event: TraceEvent) -> None:
        """Keep one session's events for consumers that project the log.

        A session that overflows is marked `None` and stays that way for the rest of its
        life. Resuming retention afterwards would rebuild a *suffix* — a log that looks
        whole and has lost its opening turns — which is the failure the cap exists to
        avoid, arriving a few thousand events later.
        """
        held = self._events.get(session_id, ...)
        if held is None:
            return                                  # overflowed earlier; stay dropped
        if held is ...:
            held = self._events[session_id] = []
        held.append(event)
        if len(held) > self._max_retained:
            logger.warning(
                f"| ⚠️ Session {session_id} passed {self._max_retained:,} retained events; "
                f"dropping its in-memory log for good. Consumers that project it will see "
                f"nothing rather than a silently truncated history."
            )
            self._events[session_id] = None

    def events(self, session_id: str) -> list[TraceEvent]:
        """One session's events in write order, or empty when not retained.

        Empty means "this process is not holding that session's log" — a session from
        another process, one that overflowed the cap, or one that has been forgotten.
        It never means "the session did nothing"; a caller that cannot tell those apart
        must not treat the result as a complete history.
        """
        return list(self._events.get(session_id) or [])

    def forget(self, session_id: str) -> None:
        """Release a finished session's in-memory log, surface, and numbering."""
        self._events.pop(session_id, None)
        self._surface.pop(session_id, None)
        self._next_seq.pop(session_id, None)

    def surface(self, session_id: str) -> list[int]:
        """The session's current surface, in history order.

        Empty for a session this process has not been emitting — the surface is live
        state, not a durable read. A caller that needs it to be authoritative must treat
        emptiness as "unknown", never as "nothing on the surface".
        """
        return list(self._surface.get(session_id) or [])

    def surface_span(self, session_id: str, start: int, end: int) -> list[int]:
        """Every surface node from ``start`` through ``end`` inclusive, in history order.

        What a producer needs before it may replace a range: a replacement has to cite
        everything it shadows, and its own records are usually only some of that. Returns
        empty when either edge is not on the surface, so a caller cannot cite a span it
        does not actually cover.
        """
        nodes = self._surface.get(session_id) or []
        try:
            i, j = nodes.index(start), nodes.index(end)
        except ValueError:
            return []
        return nodes[i : j + 1] if i <= j else []

    def subscribe(self, callback) -> None:
        """Receive every emitted event without coupling callers to a transport."""
        self._subscribers.add(callback)

    def unsubscribe(self, callback) -> None:
        self._subscribers.discard(callback)

    @property
    def writer(self) -> Optional[TraceWriter]:
        return self._writer


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

trace_manager = TraceManager()
