"""TraceWriter — async consumer that persists TraceEvents to JSON files.

One file per session: <log_root>/<session_id>.jsonl
Events are appended as newline-delimited JSON (JSONL) for streaming reads
without loading the entire file into memory.

A separate index file <log_root>/index.json maps session_id → file path
and summary metadata for the UI's session list endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.trace.types import TraceEvent
from agentevolver.utils import AsyncQueue
from agentevolver.utils.file_utils import append_jsonl, atomic_json_update


class TraceWriter:
    """Drains an AsyncQueue[TraceEvent] and writes events to JSONL files, one per session."""

    def __init__(self, log_root: str, queue: AsyncQueue[TraceEvent]) -> None:
        self._log_root = log_root
        self._queue = queue

        # session_id → summary dict for the index
        self._session_meta: Dict[str, Dict] = {}

        self._index_path = str(path_manager.under(log_root, P.LOG_TRACE_INDEX))
        self._task: Optional[asyncio.Task] = None
        self._durability_errors: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def rebind(self, log_root: str) -> None:
        """Point the writer at a new trace root (called when a session is bound).

        Subsequent events are appended under the new root, and the index follows
        it — each session keeps its own index.
        """
        if log_root == self._log_root:
            return
        self._log_root = log_root
        self._index_path = str(path_manager.under(log_root, P.LOG_TRACE_INDEX))
        self._session_meta.clear()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="trace-writer")

    async def stop(self) -> None:
        await self._queue.stop()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        await self._flush_index()

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        # Do not eagerly create the global trace root — per-session event files and
        # the index create their own dirs lazily, so no empty tag-level dir appears
        # for gateways that route every session to its own log root.
        await self._load_index()

        while True:
            event = await self._queue.get()
            if event is None:
                break
            try:
                await self._write_event(event)
            except Exception as e:
                session_id = event.session_id or "no_session"
                self._durability_errors.setdefault(session_id, str(e))
                logger.warning(f"| ⚠️  TraceWriter error: {e}")
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def _write_event(self, event: TraceEvent) -> None:
        session_id = event.session_id or "no_session"
        # One locked append is the durability boundary. Long-lived TextIO handles are
        # fast in a single process but can interleave buffered writes when two gateways
        # share a trace root.
        append_jsonl(self._session_path(session_id), event.to_dict())

        # Update session metadata
        meta = self._session_meta.setdefault(session_id, {
            "session_id": session_id,
            "file": self._session_path(session_id),
            "event_count": 0,
            "first_event_at": event.timestamp.isoformat(),
            "last_event_at": event.timestamp.isoformat(),
            "agent_names": [],
            "task_ids": [],
        })
        meta["event_count"] += 1
        if event.seq_no is not None:
            meta["last_seq_no"] = int(event.seq_no)
        meta["last_event_at"] = event.timestamp.isoformat()
        if event.agent_name and event.agent_name not in meta["agent_names"]:
            meta["agent_names"].append(event.agent_name)
        if event.task_id and event.task_id not in meta["task_ids"]:
            meta["task_ids"].append(event.task_id)

        # Flush index every 50 events to avoid hammering disk
        if meta["event_count"] % 50 == 0:
            await self._flush_index()

    def _session_path(self, session_id: str) -> str:
        safe = session_id.replace("/", "_").replace("\\", "_")
        return str(path_manager.under(
            self._log_root, P.TRACE_EVENT_LOG, session_id=safe,
        ))

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    async def _flush_index(self) -> None:
        try:
            snapshots = {
                session_id: self._rebuild_meta(session_id, fallback=meta)
                for session_id, meta in self._session_meta.items()
            }

            def merge(current):
                durable = {
                    str(item.get("session_id")): dict(item)
                    for item in dict(current or {}).get("sessions", [])
                    if isinstance(item, dict) and item.get("session_id")
                }
                for session_id, candidate in snapshots.items():
                    previous = durable.get(session_id)
                    if previous is None:
                        durable[session_id] = candidate
                        continue
                    # A process may have scanned just before waiting for the index lock.
                    # Never let that stale snapshot move the derived index backwards.
                    candidate_key = (
                        int(candidate.get("last_seq_no", -1)),
                        int(candidate.get("event_count", 0)),
                    )
                    previous_key = (
                        int(previous.get("last_seq_no", -1)),
                        int(previous.get("event_count", 0)),
                    )
                    if candidate_key >= previous_key:
                        durable[session_id] = candidate
                return {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "sessions": [durable[key] for key in sorted(durable)],
                }

            data = atomic_json_update(self._index_path, merge, default={})
            self._session_meta = {
                item["session_id"]: item for item in data.get("sessions", [])
            }
        except Exception as e:
            logger.warning(f"| ⚠️  TraceWriter index flush failed: {e}")

    def _rebuild_meta(self, session_id: str, *, fallback: Dict) -> Dict:
        """Derive index facts from the append log instead of process-local counters."""
        events = self.read_session(session_id)
        if not events:
            return dict(fallback)
        timestamps = [str(item.get("timestamp") or "") for item in events]
        sequences = [
            int(item["seq_no"]) for item in events if item.get("seq_no") is not None
        ]
        return {
            "session_id": session_id,
            "file": self._session_path(session_id),
            "event_count": len(events),
            "first_event_at": next((value for value in timestamps if value), ""),
            "last_event_at": next((value for value in reversed(timestamps) if value), ""),
            "last_seq_no": max(sequences) if sequences else len(events) - 1,
            "agent_names": sorted({
                str(item["agent_name"]) for item in events if item.get("agent_name")
            }),
            "task_ids": sorted({
                str(item["task_id"]) for item in events if item.get("task_id")
            }),
        }

    async def _load_index(self) -> None:
        if not os.path.exists(self._index_path):
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s in data.get("sessions", []):
                self._session_meta[s["session_id"]] = s
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Read-back helpers (used by API endpoints)
    # ------------------------------------------------------------------

    def list_sessions(self) -> list:
        return list(self._session_meta.values())

    def event_count(self, session_id: str) -> int:
        """Number of durably indexed events for sequence continuation."""
        return int((self._session_meta.get(session_id) or {}).get("event_count") or 0)

    def next_seq(self, session_id: str) -> int:
        """Next durable sequence, surviving gaps left by an unwritable event."""
        meta = self._session_meta.get(session_id) or {}
        if meta.get("last_seq_no") is not None:
            return int(meta["last_seq_no"]) + 1
        # Compatibility for indexes written before ``last_seq_no`` existed. Scan once
        # rather than trusting event_count: an unwritable event may have left a gap.
        events = self.read_from(session_id, after_seq=-1)
        if events:
            return max(int(event["seq_no"]) for event in events) + 1
        return 0

    def durability_error(self, session_id: str) -> Optional[str]:
        """First permanent write failure for this session, if any."""
        return self._durability_errors.get(session_id)

    def read_session(self, session_id: str) -> list:
        """Return all events for a session as a list of dicts."""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return []
        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    def read_from(
        self, session_id: str, *, after_seq: int = -1, limit: Optional[int] = None
    ) -> list:
        """Stream events whose sequence is greater than ``after_seq``.

        This is the durable boundary used by incremental projections.  It deliberately
        does not load the whole JSONL file before filtering.  Version-1 logs that predate
        ``seq_no`` use their zero-based line position as a compatibility sequence; the
        returned copy contains that effective value so a caller can persist a watermark.

        Malformed JSON lines retain the historical reader behaviour and are skipped.
        Schema compatibility is enforced one layer up by ``parse_trace_event``.
        """
        if limit is not None and int(limit) <= 0:
            return []
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return []
        events = []
        with open(path, "r", encoding="utf-8") as handle:
            for line_index, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                raw_seq = payload.get("seq_no")
                try:
                    seq = line_index if raw_seq is None else int(raw_seq)
                except (TypeError, ValueError):
                    continue
                if seq <= int(after_seq):
                    continue
                if raw_seq is None:
                    payload = {**payload, "seq_no": seq}
                events.append(payload)
                if limit is not None and len(events) >= int(limit):
                    break
        return events
