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

import asyncio
import hashlib
import inspect
import json
import os
from datetime import datetime, timezone
from typing import Optional

from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.utils import AsyncQueue
from agentevolver.trace.types import TraceEvent, parse_trace_event
from agentevolver.trace.persistence import TracePersistence, create_trace_persistence
from agentevolver.utils import Singleton


class TraceManager(metaclass=Singleton):
    """Singleton that owns the event queue and writer."""

    def __init__(self) -> None:
        self._log_root: Optional[str] = None
        self._queue: Optional[AsyncQueue[TraceEvent]] = None
        self._writer: Optional[TracePersistence] = None
        self._persistence_backend: str = "jsonl"
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
        #: Session-level permanent gaps. Queue overflow loses an event before the writer
        #: can see it; persistence providers separately retain their first write error.
        #: Neither condition can be repaired by a later flush.
        self._dropped_events: dict[str, dict[str, object]] = {}
        self._reported_integrity_gaps: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(
        self, log_root: Optional[str] = None, *, persistence: str = "jsonl"
    ) -> None:
        """Set log_root and create queue / writer.  Idempotent.

        If log_root is omitted, defaults to ``{config.log_root}/trace``.
        """
        from agentevolver.paths import path_manager as _path_manager
        _path_manager.on_rebind(self._follow_session)
        if self._initialized:
            return
        if log_root is None:
            from agentevolver.config import config
            log_root = path_manager.under(config.log_root, P.LOG_MODULE, module="trace")
        self._log_root = log_root

        self._queue = AsyncQueue[TraceEvent](maxsize=20_000)
        self._persistence_backend = str(persistence or "jsonl").lower()
        self._writer = create_trace_persistence(
            self._persistence_backend, log_root, self._queue,
        )
        self._initialized = True
        logger.info(
            f"| 🔍 TraceManager initialised "
            f"(log_root={log_root}, persistence={self._persistence_backend})"
        )

    def _follow_session(self) -> None:
        """Re-point at the newly bound session's log root.

        Subscribed to `path_manager` rather than called by the gateway. Six managers used
        to be re-pointed by name on every session change, which meant six copies of "the
        current log root" kept in step by remembering to add a line — and the forgotten
        line writes this session's files into the previous session's directory without
        erroring.
        """
        from agentevolver.paths import path_manager

        roots = path_manager.session_roots()
        if roots:
            self.rebind(str(roots["log"]))

    def rebind(self, log_root: str) -> None:
        """Re-point the trace root at ``<log_root>/trace`` for a newly bound session.

        Long-lived hosts (the Gateway) initialize this manager once, before any
        session exists; binding a session re-points it (and its writer) so each
        session's event files and index live under its own log root.
        """
        trace_root = path_manager.under(log_root, P.LOG_MODULE, module="trace")
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

    #: How long :meth:`flush` waits before giving up and letting the caller proceed.
    FLUSH_TIMEOUT_SECONDS = 5.0

    async def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until everything emitted so far has been written.

        ``emit`` returns as soon as an event is *queued*, which is what keeps it off the
        hot path and also means the log lags the world. That lag is invisible until the
        process dies inside it, and then it decides an unanswerable question: a run that
        was killed mid-step leaves no record of the tool it was about to run, so nobody can
        tell whether the destructive command executed. Calling this before an irreversible
        act closes that window.

        On timeout it gives up and returns ``False`` rather than waiting indefinitely.
        ``flush`` only reports persistence state; the semantic checkpoint policy decides
        what that state means. Interactive runs record degradation and continue, while
        training and high-risk runs fail closed before the downstream request or effect.

        Args:
            timeout: Seconds to wait; :attr:`FLUSH_TIMEOUT_SECONDS` when omitted.

        Returns:
            Whether the queue drained in time. ``True`` when there was nothing to wait for.
        """
        if not self._running or self._queue is None:
            return True
        try:
            await asyncio.wait_for(self._queue.join(), timeout or self.FLUSH_TIMEOUT_SECONDS)
            return True
        except asyncio.TimeoutError:
            logger.error(
                f"| ❌ Trace flush timed out with {self._queue.qsize()} events still queued — "
                f"the log is behind the run from here"
            )
            return False

    async def stop(self) -> None:
        """Drain queue and flush the writer."""
        if not self._running:
            return
        self._running = False

        if self._writer:
            await self._writer.stop()

        # Request pages are an observational side channel scheduled off the model
        # hot path. Give pages already in flight a bounded chance to land before a
        # short-lived direct runner exits.
        from agentevolver.visual.request_viewer import flush_request_html

        await flush_request_html()

        logger.info("| ⏹️  TraceManager stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def emit(self, event: TraceEvent) -> bool:
        """Emit a trace event and report whether it entered the persistence queue.

        Never blocks and never raises. Most observational callers may ignore the return
        value; integrity checkpoints consult the manager's permanent Session gap state.

        Stamps the event's position in its session's log before anyone sees it. The
        number is what lets one event cite another — a summary naming the range it
        replaced, a projection naming what the model saw — none of which can be
        expressed by "somewhere earlier in the file".
        """
        if not self._running or self._queue is None:
            return False
        session = event.session_id or "no_session"
        if event.seq_no is None:
            event.seq_no = self._claim_seq(session)
        # AsyncQueue returns a boolean. A few embedders use a minimal queue whose emit
        # method returns None after accepting, so only an explicit False means rejection.
        accepted = self._queue.emit(event) is not False
        if accepted:
            # A surface is the in-process view of the canonical log. Never let an event
            # rejected by the persistence queue alter that view or reach subscribers as
            # if it existed durably.
            self._apply_surface(session, event)
            self._retain(session, event)
        else:
            gap = self._dropped_events.setdefault(session, {
                "count": 0,
                "first_seq": event.seq_no,
                "last_seq": event.seq_no,
                "event_type": event.event_type.value,
            })
            gap["count"] = int(gap["count"]) + 1
            gap["last_seq"] = event.seq_no
            logger.error(
                f"| ❌ Trace queue full: dropped {event.event_type.value} "
                f"for session {session} at seq {event.seq_no}"
            )
            self._persist_integrity_issue(session, self._format_dropped_issue(gap))
        if accepted:
            for subscriber in tuple(self._subscribers):
                try:
                    result = subscriber(event)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"| ⚠️  Trace subscriber failed: {exc}")
        return accepted

    @property
    def running(self) -> bool:
        return self._running

    def integrity_issue(self, session_id: str) -> Optional[str]:
        """Describe a permanent gap that makes this Session incomplete."""
        session_id = str(session_id)
        stored = self._read_integrity_issue(session_id)
        if stored:
            return stored
        dropped = self._dropped_events.get(session_id)
        if dropped:
            issue = self._format_dropped_issue(dropped)
            self._persist_integrity_issue(session_id, issue)
            return issue
        writer = self._writer
        inspect_error = getattr(writer, "durability_error", None)
        if callable(inspect_error):
            error = inspect_error(session_id)
            if error:
                issue = f"trace persistence previously failed: {error}"
                self._persist_integrity_issue(session_id, issue)
                return issue
        return None

    @staticmethod
    def _format_dropped_issue(gap: dict[str, object]) -> str:
        return (
            f"trace queue dropped {gap['count']} event(s), sequence "
            f"{gap['first_seq']}..{gap['last_seq']}"
        )

    def _integrity_marker_path(self, session_id: str) -> Optional[str]:
        """Return a collision-resistant path without exposing Session ids as filenames."""
        if not self._log_root:
            return None
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return str(path_manager.under(
            self._log_root, P.TRACE_INTEGRITY, digest=digest,
        ))

    def _persist_integrity_issue(self, session_id: str, issue: str) -> None:
        """Seal the first known data gap so a process restart cannot forget it."""
        path = self._integrity_marker_path(session_id)
        if path is None or os.path.exists(path):
            return
        directory = os.path.dirname(path)
        temporary = f"{path}.{os.getpid()}.tmp"
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "issue": issue,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            os.makedirs(directory, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:  # noqa: BLE001 - preserve the in-memory fail-closed flag
            logger.error(f"| ❌ Could not persist Trace integrity marker: {exc}")
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    def _read_integrity_issue(self, session_id: str) -> Optional[str]:
        path = self._integrity_marker_path(session_id)
        if path is None or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("session_id") != session_id:
                return "Trace integrity marker does not match the requested session"
            issue = payload.get("issue")
            return str(issue) if issue else "Trace integrity marker is missing its issue"
        except Exception as exc:  # noqa: BLE001 - corruption cannot be treated as clean
            return f"Trace integrity marker is unreadable: {type(exc).__name__}: {exc}"

    def should_report_integrity_gap(
        self, session_id: str, boundary: str, issue: str,
    ) -> bool:
        """Deduplicate degradation facts without ever clearing the underlying gap."""
        key = (str(session_id), str(boundary), str(issue))
        if key in self._reported_integrity_gaps:
            return False
        self._reported_integrity_gaps.add(key)
        return True

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
                written = writer.next_seq(session_id)
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

    def rehydrate(self, session_id: str) -> list[TraceEvent]:
        """Restore retained events and the folded surface from durable Trace.

        Events already emitted by the new process are merged by sequence/id, which
        covers the normal ordering where the first resumed event reaches subscribers
        before memory asks for history. A malformed replacement fails closed through
        ``fold_surface`` instead of silently reconstructing a different conversation.
        """
        from agentevolver.trace.surface import fold_surface

        live = self.events(session_id)
        durable = self.read_from(session_id, after_seq=-1, durable=True)
        by_seq: dict[int, TraceEvent] = {}
        ids: set[str] = set()
        for event in [*durable, *live]:
            if event.seq_no is None:
                continue
            seq = int(event.seq_no)
            previous = by_seq.get(seq)
            if previous is not None and previous.id != event.id:
                raise ValueError(
                    f"Trace session {session_id} has conflicting events at seq {seq}"
                )
            if event.id in ids and previous is None:
                raise ValueError(
                    f"Trace session {session_id} repeats event id {event.id}"
                )
            by_seq[seq] = event
            ids.add(event.id)
        events = [by_seq[seq] for seq in sorted(by_seq)]
        if len(events) > self._max_retained:
            self._events[session_id] = None
            self._surface.pop(session_id, None)
            raise RuntimeError(
                f"Trace session {session_id} has {len(events):,} durable events, "
                f"exceeding the rehydrate limit {self._max_retained:,}"
            )
        folded = fold_surface(events)
        self._events[session_id] = events
        self._surface[session_id] = list(folded["nodes"])
        if events:
            self._next_seq[session_id] = max(
                self._next_seq.get(session_id, 0), int(events[-1].seq_no) + 1,
            )
        return list(events)

    def execution_checkpoint(
        self, session_id: str, *, workspace_fingerprint: Optional[str] = None,
    ):
        """Return the conservative resume decision derived from durable history."""
        from agentevolver.trace.execution_checkpoint import derive_execution_checkpoint

        return derive_execution_checkpoint(
            session_id,
            self.rehydrate(session_id),
            workspace_fingerprint=workspace_fingerprint,
        )

    def read_from(
        self,
        session_id: str,
        *,
        after_seq: int = -1,
        limit: Optional[int] = None,
        durable: bool = True,
    ) -> list[TraceEvent]:
        """Read a version-checked suffix for an incremental consumer.

        ``durable=True`` reads only flushed JSONL state.  A caller that needs events just
        emitted must await :meth:`flush` first; silently mixing queued and written state
        would make a watermark claim durability the source log does not yet have.
        """
        if durable and self._writer is not None:
            payloads = self._writer.read_from(
                session_id, after_seq=after_seq, limit=limit,
            )
            parsed = []
            for payload in payloads:
                event = parse_trace_event(payload)
                if event is not None:
                    parsed.append(event)
            return parsed
        events = [
            event for event in self.events(session_id)
            if event.seq_no is not None and event.seq_no > after_seq
        ]
        return events if limit is None else events[:max(0, int(limit))]

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
    def writer(self) -> Optional[TracePersistence]:
        return self._writer

    @property
    def log_root(self) -> Optional[str]:
        """Durable root used by persistence providers and projection state."""
        return self._log_root


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

trace_manager = TraceManager()
