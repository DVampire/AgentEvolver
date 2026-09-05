"""One process's inbox: FIFO, bounded only by memory, drained at safe points.

Built on a deque plus an event rather than :class:`asyncio.Queue` for one reason: a
process parked here must be able to wake on a *signal* too, and racing a queue's
``get()`` against another awaitable means either cancelling a coroutine that has
already dequeued an item, or losing it. An explicit ``wait()`` that only reports
non-emptiness lets the caller decide what to take, and take it synchronously.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Deque, List, Optional

from agentevolver.runtime.envelopes import Envelope
from agentevolver.runtime.errors import MailboxClosed


class Mailbox:
    """FIFO inbox for one process."""

    __slots__ = ("_items", "_arrived", "_closed", "_owner", "_path", "_lock", "_state")

    def __init__(self, owner: str = "") -> None:
        self._items: Deque[Envelope] = deque()
        self._arrived: asyncio.Event = asyncio.Event()
        self._closed: bool = False
        self._owner: str = owner
        self._path = None
        self._lock = None
        self._state = {"version": 2, "identity": {}, "topics": [], "messages": {}, "turns": {}}

    def bind(self, path, *, identity: dict, topics=(), resume=False) -> None:
        """Claim a durable endpoint. Never replay a possibly executed operation.

        The caller recreates the agent using the same thread identity. A process
        lock excludes concurrent resumes; queued messages and subscriptions survive
        a crash. Received/interrupted/failed operations require explicit reconciliation.
        """
        import fcntl

        if self._lock is not None:
            raise RuntimeError("Mailbox is already bound")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.with_suffix(path.suffix + ".owner").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if path.exists():
                if not resume:
                    raise RuntimeError(f"Durable endpoint exists; use resume: {path}")
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("version") == 1:
                    raise ValueError("Legacy mailbox lacks durable turn results; explicit migration is required")
                if state.get("version") != 2 or state.get("identity") != identity:
                    raise ValueError("Mailbox recovery identity/version mismatch")
                messages = state["messages"]
                if not isinstance(state["topics"], list) or any(not isinstance(t, str) for t in state["topics"]):
                    raise ValueError("Invalid persisted subscriptions")
                for key, record in messages.items():
                    if self._decode(record["envelope"]).id != key:
                        raise ValueError("Mailbox message identity mismatch")
                    if record["status"] not in {"queued", "delivered", "undelivered", "unhandled"}:
                        raise RuntimeError(
                            f"Uncertain delivery {record['envelope']['id']}; reconcile before resume: {path}"
                        )
                turns = state["turns"]
                if not isinstance(turns, dict):
                    raise ValueError("Invalid persisted turns")
                for key, turn in turns.items():
                    if (not key.isdigit() or int(key) < 1 or str(int(key)) != key
                            or not isinstance(turn, dict)
                            or not isinstance(turn.get("message"), str)
                            or type(turn.get("success")) is not bool
                            or messages.get(turn.get("envelope"), {}).get("status") != "delivered"):
                        raise ValueError("Invalid persisted turn receipt")
                if sorted(map(int, turns)) != list(range(1, len(turns) + 1)):
                    raise ValueError("Persisted turns must be contiguous")
                self._state = state
            else:
                if resume:
                    raise FileNotFoundError(f"No durable endpoint to resume: {path}")
                self._state.update(identity=identity, topics=sorted(set(topics)))
            self._path, self._lock = path, handle
            self._save(self._state)
            for record in self._state["messages"].values():
                if record["status"] == "queued":
                    self._items.append(self._decode(record["envelope"]))
            if self._items:
                self._arrived.set()
        except BaseException:
            handle.close()
            self._path = self._lock = None
            raise

    @staticmethod
    def _decode(data):
        from agentevolver.runtime.envelopes import TaskEnvelope, EventEnvelope, ReportEnvelope, ReplyEnvelope

        kinds = {kind.__name__: kind for kind in (TaskEnvelope, EventEnvelope, ReportEnvelope, ReplyEnvelope)}
        values = dict(data)
        kind = kinds[values.pop("kind")]
        return kind(**values)

    def _save(self, state):
        from agentevolver.utils.file_utils import atomic_write_text

        if self._path is not None:
            if self._lock is None:
                raise RuntimeError("Mailbox ownership has been released")
            atomic_write_text(self._path, json.dumps(state, ensure_ascii=False, allow_nan=False))
        self._state = state

    @property
    def topics(self):
        return tuple(self._state["topics"])

    def subscribe(self, topic, *, remove=False):
        topics = set(self.topics)
        topics.discard(topic) if remove else topics.add(topic)
        self._save({**self._state, "topics": sorted(topics)})

    @property
    def turns(self):
        return {int(key): dict(value) for key, value in self._state.get("turns", {}).items()}

    def receipt(self, envelope, status, *, turn=None):
        # Write before performing the corresponding in-memory transition or action.
        self.known(envelope)
        record = {"status": status, "envelope": {"kind": type(envelope).__name__, **asdict(envelope)}}
        state = {**self._state, "messages": {**self._state["messages"], envelope.id: record}}
        if turn is not None:
            index, message, success = turn
            if status != "delivered" or index != len(self._state.get("turns", {})) + 1:
                raise ValueError("Turn completion must be delivered in sequence")
            state["turns"] = {**self._state.get("turns", {}), str(index): {
                "envelope": envelope.id, "message": message, "success": success,
            }}
        self._save(state)

    def delivered(self, message_id):
        return self._state["messages"].get(message_id, {}).get("status")

    def known(self, envelope):
        record = self._state["messages"].get(envelope.id)
        if record is not None and record["envelope"] != {"kind": type(envelope).__name__, **asdict(envelope)}:
            raise ValueError("A message ID cannot be reused for different content")
        return record["status"] if record else None

    @staticmethod
    def reconcile(path, message_id, *, replay: bool, evidence: str):
        """Host-only crash reconciliation; never exposed as an agent tool.

        Replay requires the operator to confirm no side effect occurred (or that
        the receiver is idempotent). Otherwise acknowledge the completed operation.
        """
        import fcntl
        from agentevolver.utils.file_utils import atomic_write_text

        if not evidence.strip() or type(replay) is not bool:
            raise ValueError("Reconciliation requires evidence and an explicit replay decision")
        path = Path(path)
        with path.with_suffix(path.suffix + ".owner").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = json.loads(path.read_text(encoding="utf-8"))
            record = state["messages"][message_id]
            if record["status"] not in {"received", "interrupted", "failed", "undelivered"}:
                raise ValueError("Only uncertain or undelivered messages need reconciliation")
            record.update(status="queued" if replay else "delivered", reconciliation=evidence)
            atomic_write_text(path, json.dumps(state, ensure_ascii=False, allow_nan=False))

    def release(self):
        """Release endpoint ownership only after the process finishes cleanup."""
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    # -- writing -------------------------------------------------------------

    def put(self, envelope: Envelope) -> None:
        """Append a message and wake anything waiting.

        Raises:
            MailboxClosed: The owning process has exited.
        """
        if self._closed:
            raise MailboxClosed(
                f"process {self._owner or '?'} has exited; it cannot receive "
                f"{envelope.summary()}"
            )
        known = self.known(envelope)
        if known is not None:
            return
        self.receipt(envelope, "queued")
        self._items.append(self._decode(self._state["messages"][envelope.id]["envelope"]))
        self._arrived.set()

    # -- reading -------------------------------------------------------------

    def take(self) -> Optional[Envelope]:
        """Pop the oldest message, or None when empty. Never blocks."""
        if not self._items:
            self._arrived.clear()
            return None
        envelope = self._items[0]
        self.receipt(envelope, "received")
        self._items.popleft()
        if not self._items:
            self._arrived.clear()
        return envelope

    async def wait(self) -> None:
        """Block until at least one message is present.

        Returns immediately when the mailbox is already non-empty. Callers that must
        also react to a signal race this against ``SignalBox.arrived.wait()``.
        """
        if self._items:
            return
        await self._arrived.wait()

    def drain(self) -> List[Envelope]:
        """Take everything queued, oldest first."""
        items = list(self._items)
        self._items.clear()
        self._arrived.clear()
        return items

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> List[Envelope]:
        """Refuse further writes and return whatever was still queued.

        The undelivered remainder is returned rather than dropped silently so the
        kernel can log what a dying process never got to read.
        """
        self._closed = True
        remaining = self.drain()
        # Wake any waiter so it can observe the closure instead of hanging.
        self._arrived.set()
        return remaining

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        # Without this, `if mailbox:` would follow __len__ and read as "has messages",
        # which is true but reads at the call site as "has a mailbox".
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "closed" if self._closed else "open"
        return f"Mailbox(owner={self._owner!r}, queued={len(self._items)}, {state})"


__all__ = ["Mailbox"]
