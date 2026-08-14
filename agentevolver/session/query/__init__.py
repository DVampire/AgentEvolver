"""Read a finished run's trace log back — the only way an agent learns from a past one."""

from .server import (
    DEFAULT_HITS,
    DEFAULT_OUTLINE,
    MAX_HITS,
    MAX_OUTLINE,
    MAX_RUNS_SCANNED,
    MAX_WINDOW,
    SessionQueryServer,
    session_query,
)
from .types import (
    EventHit,
    EventWindow,
    SearchPage,
    SessionHit,
    SessionOutline,
    SessionRecord,
)

__all__ = [
    "DEFAULT_HITS",
    "DEFAULT_OUTLINE",
    "MAX_HITS",
    "MAX_OUTLINE",
    "MAX_RUNS_SCANNED",
    "MAX_WINDOW",
    "EventHit",
    "EventWindow",
    "SearchPage",
    "SessionHit",
    "SessionOutline",
    "SessionQueryServer",
    "SessionRecord",
    "session_query",
]
