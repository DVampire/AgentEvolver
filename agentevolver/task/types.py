"""Task, scheduling, document, and standing-goal contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from agentevolver.utils import make_id


class TaskStatus(str, Enum):
    """Lifecycle states for a Task."""

    PENDING = "pending"
    WAITING_CONFIRMATION = "waiting_confirmation"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    """Priority levels for task scheduling.

    Higher numeric value → higher priority.
    """

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class Task(BaseModel):
    """Top-level unit of work submitted to ``TaskManagerServer``.

    ``session_id`` binds work to its Session so memory, budgets, and workspace
    state do not bleed between concurrent tasks.
    """

    id: str = Field(
        default_factory=lambda: make_id(),
        description="Unique identifier for this task.",
    )
    content: str = Field(description="Natural-language description of what needs to be done.")
    files: List[str] = Field(
        default_factory=list,
        description="Optional list of file paths attached to the task.",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.NORMAL,
        description="Scheduling priority; higher value processed first.",
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="Current lifecycle state of the task.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Session this task is bound to.  When set, the bus routes the task "
            "through the session-isolated worker for that session_id, ensuring "
            "memory, todo lists, and working directory do not bleed across tasks."
        ),
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value pairs for caller-specific context.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the task was created.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the last status change.",
    )

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.updated_at = datetime.now(timezone.utc)

    def mark_waiting_confirmation(self) -> None:
        """Park interrupted work until a human reconciles an uncertain effect."""
        self.status = TaskStatus.WAITING_CONFIRMATION
        self.updated_at = datetime.now(timezone.utc)

    def mark_done(self) -> None:
        self.status = TaskStatus.DONE
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self) -> None:
        self.status = TaskStatus.FAILED
        self.updated_at = datetime.now(timezone.utc)

    def mark_cancelled(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.updated_at = datetime.now(timezone.utc)


class TaskCategory(str, Enum):
    """Logical category controlling scheduler weight."""

    USER = "user"
    EVOLVER = "evolver"


class TaskRecord(BaseModel):
    """Persisted scheduling envelope around one Task."""

    task: Task
    category: TaskCategory = TaskCategory.USER
    entity_key: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    read_set: List[str] = Field(default_factory=list)
    write_set: List[str] = Field(default_factory=list)
    owner: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    token_budget: Optional[int] = None
    acceptance: List[str] = Field(default_factory=list)
    recovered_from_running: bool = False
    result: Optional[Any] = None
    error: Optional[str] = None
    max_retries: int = 0
    retry_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def effective_priority(self) -> int:
        """Return the PriorityQueue value; lower values run first."""
        base = -self.task.priority.value
        category_penalty = 0 if self.category is TaskCategory.USER else 1000
        return base + category_penalty


class TaskDeferred(RuntimeError):
    """A handler cannot continue until an external decision is recorded."""

    def __init__(self, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass
class TaskDocument:
    """One authored task rendered for both the Agent and visual layer."""

    content: str
    html_body: str
    type: Literal["html", "md"]
    source_path: str
    title: str


# ---------------------------------------------------------------------------
# Goals — the standing objective a session is judged against
# ---------------------------------------------------------------------------
#
# A Task is one submission: it is created, it runs, it ends, and the run ends
# with it. A Goal outlives every task in the session and says what the session
# is *for*. The two differ in who may change them, which is the whole point of
# the type: a task is the agent's to run, a goal is the human's to set.


class GoalPhase(str, Enum):
    """Where a goal stands.

    ``BLOCKED`` is not a failure and not an ending — the objective still holds
    and something outside the agent has to move. Collapsing it into ``COMPLETE``
    would let a stuck run report the goal as met.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETE = "complete"

    @property
    def is_open(self) -> bool:
        """Whether the goal still stands. Only completion closes it."""
        return self is not GoalPhase.COMPLETE


class GoalAuthority(str, Enum):
    """Who is asking for a change.

    Never a tool argument. The model can write anything into its own arguments,
    so an authority it can name is an authority it holds; this value is derived
    from the calling context by :func:`agentevolver.task.goal.authority_of` and
    passed in by the host.
    """

    HUMAN = "human"
    AGENT = "agent"


class GoalAction(str, Enum):
    """The changes an existing goal admits."""

    EDIT = "edit"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    BLOCKED = "blocked"


#: Actions that require a direct human, alongside creating a goal at all.
#:
#: This is the line the whole feature exists to draw. An agent that may rewrite
#: its objective has a note, not a goal: whenever the work got hard it could
#: edit the target to whatever it had already achieved, and its own trajectory
#: would read as success. Reporting *progress* is different — the agent is the
#: only party that knows whether the objective was met or whether it is stuck —
#: so ``COMPLETE`` and ``BLOCKED`` are open to it, and are claims a human can
#: still read and overturn.
HUMAN_ONLY_ACTIONS = frozenset({GoalAction.EDIT, GoalAction.PAUSE, GoalAction.RESUME})


class Goal(BaseModel):
    """One standing objective, as it is persisted.

    ``revision`` is the compare-and-set token. Every mutation increments it, and
    a mutation must name the revision it read, so a caller working from a stale
    view is told rather than silently overwriting a change it never saw.
    """

    id: str = Field(default_factory=lambda: f"goal_{make_id()}",
                    description="Stable identity across every revision of this goal.")
    session_id: str = Field(default="", description="The session this goal belongs to.")
    objective: str = Field(description="What the human asked for, in their words.")
    phase: GoalPhase = Field(default=GoalPhase.ACTIVE)
    revision: int = Field(default=1, description="Incremented by every durable change; the compare-and-set token.")
    priority: TaskPriority = Field(default=TaskPriority.NORMAL,
                                   description="Same scale tasks use, so a goal and the tasks under it can be compared.")
    blocked_reason: Optional[str] = Field(
        default=None,
        description="Set only while phase is blocked: the concrete condition that has to change.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        """One line, for a listing or a tool result."""
        state = self.phase.value
        if self.phase is GoalPhase.BLOCKED and self.blocked_reason:
            state = f"blocked: {self.blocked_reason[:60]}"
        return f"{self.id} (rev {self.revision})  {state}  {self.objective[:80]}"


class GoalError(Exception):
    """Base for every refusal the goal store issues."""


class GoalAuthorityError(GoalError):
    """The caller may not make this change. Raised, never returned as a value.

    A refusal that came back as an ordinary result would be one `if` away from
    being ignored by a caller that only checks for exceptions.
    """


class GoalRevisionError(GoalError):
    """The caller wrote against a revision that is no longer current."""


class GoalStateError(GoalError):
    """The change does not apply to the goal in its current phase."""
