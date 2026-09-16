"""Pluggable durable storage for Trace events.

The queue consumer is part of the persistence contract because durability ordering is
defined at that boundary: ``flush`` waits for the consumer, and a projection watermark
may only advance past events that consumer has committed.  JSONL remains the readable
default; :class:`SQLiteTracePersistence` is the indexed alternative for large runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from typing import Any, Optional, Protocol, runtime_checkable

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.trace.types import TraceEvent
from agentevolver.utils import AsyncQueue


@runtime_checkable
class TracePersistence(Protocol):
    """Lifecycle and query surface required by :class:`TraceManager`."""

    def rebind(self, log_root: str) -> None: ...
    def start(self) -> None: ...
    async def stop(self) -> None: ...
    def list_sessions(self) -> list: ...
    def read_session(self, session_id: str) -> list: ...
    def read_from(
        self, session_id: str, *, after_seq: int = -1, limit: Optional[int] = None
    ) -> list: ...
    def event_count(self, session_id: str) -> int: ...
    def next_seq(self, session_id: str) -> int: ...
    def durability_error(self, session_id: str) -> Optional[str]: ...


class SQLiteTracePersistence:
    """Indexed Trace storage using only Python's standard-library SQLite driver."""

    def __init__(self, log_root: str, queue: AsyncQueue[TraceEvent]) -> None:
        self._log_root = str(log_root)
        self._queue = queue
        self._task: Optional[asyncio.Task] = None
        self._connection: Optional[sqlite3.Connection] = None
        # A failed event cannot be recreated by a later successful write. Keep the first
        # failure for the lifetime of this provider so strict integrity cannot mistake
        # "the queue drained" for "the complete session is durable".
        self._durability_errors: dict[str, str] = {}

    @property
    def database_path(self) -> str:
        return str(path_manager.under(self._log_root, P.TRACE_SQLITE))

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            os.makedirs(self._log_root, exist_ok=True)
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    session_id TEXT NOT NULL,
                    seq_no INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (session_id, seq_no)
                );
                CREATE INDEX IF NOT EXISTS trace_events_timestamp
                    ON trace_events(timestamp);
                CREATE TABLE IF NOT EXISTS trace_sessions (
                    session_id TEXT PRIMARY KEY,
                    event_count INTEGER NOT NULL,
                    first_event_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    agent_names TEXT NOT NULL,
                    task_ids TEXT NOT NULL
                );
                """
            )
            connection.commit()
            self._connection = connection
        return self._connection

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def rebind(self, log_root: str) -> None:
        if str(log_root) == self._log_root:
            return
        self._close()
        self._log_root = str(log_root)

    def start(self) -> None:
        self._connect()
        self._task = asyncio.create_task(self._run(), name="trace-sqlite-writer")

    async def stop(self) -> None:
        await self._queue.stop()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._close()

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            try:
                self._write_event(event)
            except Exception as exc:  # noqa: BLE001 - tracing remains observational
                session_id = event.session_id or "no_session"
                self._durability_errors.setdefault(session_id, str(exc))
                logger.warning(f"| ⚠️ SQLite Trace persistence error: {exc}")
            finally:
                self._queue.task_done()

    def _write_event(self, event: TraceEvent) -> None:
        session_id = event.session_id or "no_session"
        connection = self._connect()
        if event.seq_no is None:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq_no), -1) + 1 FROM trace_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            event.seq_no = int(row[0])
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        timestamp = event.timestamp.isoformat()
        current = connection.execute(
            "SELECT * FROM trace_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        agents = set(json.loads(current["agent_names"])) if current else set()
        tasks = set(json.loads(current["task_ids"])) if current else set()
        if event.agent_name:
            agents.add(event.agent_name)
        if event.task_id:
            tasks.add(event.task_id)
        with connection:
            connection.execute(
                "INSERT INTO trace_events(session_id, seq_no, timestamp, payload) "
                "VALUES (?, ?, ?, ?)",
                (session_id, int(event.seq_no), timestamp, payload),
            )
            connection.execute(
                """
                INSERT INTO trace_sessions(
                    session_id, event_count, first_event_at, last_event_at,
                    agent_names, task_ids
                ) VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    event_count = trace_sessions.event_count + 1,
                    last_event_at = excluded.last_event_at,
                    agent_names = excluded.agent_names,
                    task_ids = excluded.task_ids
                """,
                (
                    session_id,
                    current["first_event_at"] if current else timestamp,
                    timestamp,
                    json.dumps(sorted(agents), ensure_ascii=False),
                    json.dumps(sorted(tasks), ensure_ascii=False),
                ),
            )

    def list_sessions(self) -> list:
        rows = self._connect().execute(
            "SELECT * FROM trace_sessions ORDER BY first_event_at"
        ).fetchall()
        return [{
            "session_id": row["session_id"],
            "file": self.database_path,
            "event_count": row["event_count"],
            "first_event_at": row["first_event_at"],
            "last_event_at": row["last_event_at"],
            "agent_names": json.loads(row["agent_names"]),
            "task_ids": json.loads(row["task_ids"]),
        } for row in rows]

    def read_session(self, session_id: str) -> list:
        return self.read_from(session_id, after_seq=-1)

    def read_from(
        self, session_id: str, *, after_seq: int = -1, limit: Optional[int] = None
    ) -> list:
        if limit is not None and int(limit) <= 0:
            return []
        sql = (
            "SELECT payload FROM trace_events WHERE session_id = ? AND seq_no > ? "
            "ORDER BY seq_no"
        )
        parameters: list[Any] = [session_id, int(after_seq)]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(int(limit))
        rows = self._connect().execute(sql, parameters).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def event_count(self, session_id: str) -> int:
        row = self._connect().execute(
            "SELECT event_count FROM trace_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def next_seq(self, session_id: str) -> int:
        row = self._connect().execute(
            "SELECT COALESCE(MAX(seq_no), -1) + 1 FROM trace_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0])

    def durability_error(self, session_id: str) -> Optional[str]:
        """First permanent write failure for this session, if any."""
        return self._durability_errors.get(session_id)


def create_trace_persistence(
    backend: str, log_root: str, queue: AsyncQueue[TraceEvent]
) -> TracePersistence:
    """Construct a built-in persistence provider from a stable configuration name."""
    selected = str(backend or "jsonl").lower()
    if selected == "jsonl":
        from agentevolver.trace.writer import TraceWriter
        return TraceWriter(log_root=log_root, queue=queue)
    if selected == "sqlite":
        return SQLiteTracePersistence(log_root=log_root, queue=queue)
    raise ValueError(f"unknown trace persistence backend {backend!r}; use 'jsonl' or 'sqlite'")


__all__ = [
    "TracePersistence",
    "SQLiteTracePersistence",
    "create_trace_persistence",
]
