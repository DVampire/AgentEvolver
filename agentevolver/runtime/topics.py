"""Topic subscriptions: which processes hear which broadcasts.

Kept apart from the kernel because it is pure bookkeeping — a bidirectional index and
nothing else. Delivery, waking an idle subscriber and fan-out counting all need the
process table, so they live in :class:`~agentevolver.runtime.kernel.Kernel`.

The reverse index is not an optimisation. A process that exits must lose every edge it
holds, and finding them by scanning all topics is how a dead subscriber keeps receiving
events until something notices.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set, Tuple

#: A logical topic name as a model may write it. The scope is added, never typed.
TOPIC_NAME = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def scoped(topic: str, ctx: Any) -> str:
    """A logical topic name, scoped to the task tree that owns it.

    A model supplies the stable logical name — ``website.releases`` — and the scope is
    added here. Without it two people running the same workflow publish into each
    other's subscribers, which is the kind of fault that looks like a model error.

    Raises:
        ValueError: The name is malformed, or the context carries no session identity.
    """
    logical = str(topic or "").strip()
    if not TOPIC_NAME.fullmatch(logical):
        raise ValueError(
            "topic must be 1-128 characters and contain only letters, digits, dot, "
            "underscore, colon, or hyphen"
        )
    extra = getattr(ctx, "extra", None) or {}
    root = str(extra.get("root_session_id") or getattr(ctx, "id", "") or "").strip()
    if not root:
        raise ValueError("publish/subscribe requires a session identity")
    return f"{root}::{logical}"


class TopicRegistry:
    """A bidirectional topic ↔ pid index."""

    __slots__ = ("_by_topic", "_by_pid")

    def __init__(self) -> None:
        self._by_topic: Dict[str, Set[str]] = {}
        self._by_pid: Dict[str, Set[str]] = {}

    def subscribe(self, pid: str, topic: str) -> bool:
        """Add one edge. Returns False when it already existed."""
        topic = str(topic).strip()
        if not topic:
            raise ValueError("a topic name cannot be empty")
        subscribers = self._by_topic.setdefault(topic, set())
        if pid in subscribers:
            return False
        subscribers.add(pid)
        self._by_pid.setdefault(pid, set()).add(topic)
        return True

    def subscribe_many(self, pid: str, topics: Iterable[str]) -> int:
        """Add several edges; returns how many were new."""
        return sum(1 for topic in topics if self.subscribe(pid, topic))

    def unsubscribe(self, pid: str, topic: str) -> bool:
        """Remove one edge. Returns False when it was not there."""
        subscribers = self._by_topic.get(topic)
        if not subscribers or pid not in subscribers:
            return False
        subscribers.discard(pid)
        if not subscribers:
            self._by_topic.pop(topic, None)
        held = self._by_pid.get(pid)
        if held is not None:
            held.discard(topic)
            if not held:
                self._by_pid.pop(pid, None)
        return True

    def drop(self, pid: str) -> List[str]:
        """Remove every edge this process holds. Called when it exits."""
        topics = sorted(self._by_pid.pop(pid, set()))
        for topic in topics:
            subscribers = self._by_topic.get(topic)
            if subscribers is None:
                continue
            subscribers.discard(pid)
            if not subscribers:
                self._by_topic.pop(topic, None)
        return topics

    def subscribers(self, topic: str) -> Tuple[str, ...]:
        """pids listening to ``topic``, in a stable order."""
        return tuple(sorted(self._by_topic.get(topic, set())))

    def topics(self, pid: str) -> Tuple[str, ...]:
        """Topics ``pid`` listens to, in a stable order."""
        return tuple(sorted(self._by_pid.get(pid, set())))

    def all_topics(self) -> Tuple[str, ...]:
        return tuple(sorted(self._by_topic))

    def __len__(self) -> int:
        return sum(len(subscribers) for subscribers in self._by_topic.values())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TopicRegistry(topics={len(self._by_topic)}, edges={len(self)})"


__all__ = ["TOPIC_NAME", "TopicRegistry", "scoped"]
