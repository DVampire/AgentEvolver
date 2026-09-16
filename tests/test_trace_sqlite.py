"""SQLite is an indexed implementation of the same Trace persistence contract."""

from __future__ import annotations

import sqlite3

import pytest

from agentevolver.trace.persistence import SQLiteTracePersistence, create_trace_persistence
from agentevolver.trace.server import TraceManager
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.utils import AsyncQueue


def _event(session: str, seq: int, *, agent: str = "agent", task: str = "task"):
    return TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id=session,
        seq_no=seq,
        agent_name=agent,
        task_id=task,
        label=f"event-{seq}",
    )


@pytest.mark.asyncio
async def test_sqlite_persistence_appends_lists_and_reads_an_indexed_suffix(tmp_path):
    queue = AsyncQueue()
    persistence = SQLiteTracePersistence(str(tmp_path / "trace"), queue)
    for seq in range(6):
        queue.emit(_event("session", seq, agent=f"agent-{seq % 2}", task=f"task-{seq % 2}"))

    persistence.start()
    await persistence.stop()

    assert persistence.event_count("session") == 6
    assert [
        row["seq_no"]
        for row in persistence.read_from(
            "session",
            after_seq=2,
            limit=2,
        )
    ] == [3, 4]
    summary = persistence.list_sessions()[0]
    assert summary["event_count"] == 6
    assert summary["agent_names"] == ["agent-0", "agent-1"]
    assert summary["task_ids"] == ["task-0", "task-1"]


@pytest.mark.asyncio
async def test_sqlite_rejects_duplicate_sequence_numbers_instead_of_overwriting(tmp_path):
    queue = AsyncQueue()
    persistence = SQLiteTracePersistence(str(tmp_path / "trace"), queue)
    persistence._write_event(_event("session", 0))

    with pytest.raises(sqlite3.IntegrityError):
        persistence._write_event(_event("session", 0))

    assert len(persistence.read_session("session")) == 1
    persistence._close()


def test_sqlite_next_sequence_uses_the_max_not_the_row_count(tmp_path):
    persistence = SQLiteTracePersistence(str(tmp_path / "trace"), AsyncQueue())
    persistence._write_event(_event("session", 7))

    assert persistence.event_count("session") == 1
    assert persistence.next_seq("session") == 8
    persistence._close()


def test_persistence_factory_keeps_jsonl_default_and_validates_names(tmp_path):
    queue = AsyncQueue()
    jsonl = create_trace_persistence("jsonl", str(tmp_path / "jsonl"), queue)
    sqlite = create_trace_persistence("sqlite", str(tmp_path / "sqlite"), queue)

    assert type(jsonl).__name__ == "TraceWriter"
    assert isinstance(sqlite, SQLiteTracePersistence)
    with pytest.raises(ValueError, match="unknown trace persistence"):
        create_trace_persistence("memory-ish", str(tmp_path), queue)


@pytest.mark.asyncio
async def test_trace_manager_can_run_on_sqlite_without_changing_emit_or_read_api(tmp_path):
    class IsolatedTraceManager(TraceManager):
        pass

    manager = IsolatedTraceManager()
    await manager.initialize(str(tmp_path / "trace"), persistence="sqlite")
    await manager.start()
    event = _event("session", 0)
    event.seq_no = None
    await manager.emit(event)
    assert await manager.flush()

    observed = manager.read_from("session", after_seq=-1)
    await manager.stop()

    assert isinstance(manager.writer, SQLiteTracePersistence)
    assert [event.seq_no for event in observed] == [0]
