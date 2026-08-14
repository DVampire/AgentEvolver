"""GoalStore — the session's standing objective, and who is allowed to move it.

Storage is the boring half. The half that matters is authority: creating a goal,
rewriting its objective, pausing it and resuming it are a human's to do, while
reporting it complete or blocked is the agent's. That split is enforced here, at
the store, not in the tool layer — a rule that lives in the tool is a rule the
next caller can route around, and there will be a next caller.

The authority itself is never an argument the model supplies. It is derived from
the calling context by :func:`authority_of`, from a stamp the host writes when it
accepts a human request. A model that could name its own authority would have it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.task.types import (
    HUMAN_ONLY_ACTIONS,
    Goal,
    GoalAction,
    GoalAuthority,
    GoalAuthorityError,
    GoalPhase,
    GoalRevisionError,
    GoalStateError,
    TaskPriority,
)
from agentevolver.utils import Singleton

#: Context key carrying the host's attestation that this run serves a request a
#: human actually made. Written by whoever accepted that request (the gateway
#: writes it for a ``TaskCategory.USER`` task); absent everywhere else.
#:
#: A stamp rather than an inference. "Is there a human behind this turn" cannot
#: be recovered downstream — an evolver task and a typed message reach the agent
#: as the same string — so the only party that can answer it is the one that took
#: the request in.
HUMAN_TURN_KEY = "human_turn"

#: Context key naming the owner whose tree this session lives in. Only used to
#: resolve the goal file; missing means the single-user default.
OWNER_KEY = "owner"

#: Lineage keys. A sub-agent inherits its parent's execution environment, and it
#: must not inherit the parent's authority with it: a dispatched sub-agent is the
#: system talking to itself, however human the request that started the parent.
_PARENT_KEYS = ("parent_session_id",)

DEFAULT_OWNER = "local"


def _extra_of(ctx: Any) -> Dict[str, Any]:
    return dict(getattr(ctx, "extra", None) or {})


def owner_of(ctx: Any) -> str:
    """Whose tree this context's session lives in."""
    return str(_extra_of(ctx).get(OWNER_KEY) or DEFAULT_OWNER)


def session_of(ctx: Any) -> str:
    """Which session's goals this context sees.

    ``ctx.id`` follows the conversation, and a project can hold several — but the
    goal belongs to the project, so two lines of dialogue about the same work must
    not each get their own objective. ``project_id`` is that identity when the host
    sets it; a run without one has exactly one session, which is ``ctx.id``.
    """
    extra = _extra_of(ctx)
    return str(extra.get("project_id") or getattr(ctx, "id", "") or "direct")


def authority_of(ctx: Any) -> GoalAuthority:
    """What the caller behind this context may change.

    Two facts, both attested by the host and neither reachable from a tool
    argument: the context is not a dispatched sub-agent, and the host stamped
    this run as serving a direct human request.
    """
    if any(getattr(ctx, key, None) or _extra_of(ctx).get(key) for key in _PARENT_KEYS):
        return GoalAuthority.AGENT
    return (GoalAuthority.HUMAN if _extra_of(ctx).get(HUMAN_TURN_KEY) is True
            else GoalAuthority.AGENT)


class GoalStore(metaclass=Singleton):
    """Reads and writes one session's goals, and refuses changes it must refuse."""

    #: Read for every timestamp, so a test can pin time instead of waiting for it.
    clock: Callable[[], datetime] = staticmethod(lambda: datetime.now(timezone.utc))

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def current(self, session_id: str, *, owner: str = DEFAULT_OWNER) -> Optional[Goal]:
        """The goal that still stands, or the most recent one if all are complete.

        A finished goal is still worth returning: the next thing a human says is
        often about it, and answering "no goal" a minute after one was completed
        makes the session look like it never had one.
        """
        goals = self.history(session_id, owner=owner)
        if not goals:
            return None
        for goal in reversed(goals):
            if goal.phase.is_open:
                return goal
        return goals[-1]

    def history(self, session_id: str, *, owner: str = DEFAULT_OWNER) -> List[Goal]:
        """Every goal this session has had, oldest first."""
        return self._load(owner, session_id)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        session_id: str,
        objective: str,
        authority: GoalAuthority,
        owner: str = DEFAULT_OWNER,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> Goal:
        """Record a new goal. Direct human authority only.

        Refused while another goal is still open, and the refusal names it. Two
        live goals mean two answers to "what is this session for", and the agent
        would be free to pick whichever it was closer to reaching.
        """
        if authority is not GoalAuthority.HUMAN:
            raise GoalAuthorityError(
                "Creating a goal takes a direct human request. Ask the person you are "
                "working for to state the objective; then it can be created."
            )
        text = (objective or "").strip()
        if not text:
            raise GoalStateError("A goal needs an objective; the text was empty.")

        with self._lock:
            goals = self._load(owner, session_id)
            open_goal = next((g for g in goals if g.phase.is_open), None)
            if open_goal is not None:
                raise GoalStateError(
                    f"{open_goal.id} is still {open_goal.phase.value}: "
                    f"{open_goal.objective[:80]!r}. Complete it before creating another."
                )
            now = self.clock()
            goal = Goal(session_id=session_id, objective=text, priority=priority,
                        created_at=now, updated_at=now)
            goals.append(goal)
            self._save(owner, session_id, goals)
        logger.info(f"| 🎯 Goal {goal.id} created in {session_id}: {text[:80]}")
        return goal

    def update(
        self,
        *,
        session_id: str,
        goal_id: str,
        revision: int,
        action: GoalAction,
        authority: GoalAuthority,
        owner: str = DEFAULT_OWNER,
        objective: Optional[str] = None,
        blocked_reason: Optional[str] = None,
        priority: Optional[TaskPriority] = None,
    ) -> Goal:
        """Apply one change to one exact revision of one goal."""
        if action in HUMAN_ONLY_ACTIONS and authority is not GoalAuthority.HUMAN:
            raise GoalAuthorityError(
                f"'{action.value}' takes a direct human request. You may report where the "
                f"goal stands — 'complete' or 'blocked' — but not change what it asks for."
            )

        with self._lock:
            goals = self._load(owner, session_id)
            index = next((i for i, g in enumerate(goals) if g.id == goal_id), None)
            if index is None:
                known = ", ".join(g.id for g in goals) or "(none)"
                raise GoalStateError(f"No goal {goal_id!r} in this session. Known: {known}")

            goal = goals[index]
            if goal.revision != revision:
                raise GoalRevisionError(
                    f"{goal_id} is at revision {goal.revision}, not {revision}. Read it "
                    f"again — it changed since you looked, and the change may be the answer."
                )

            updated = self._apply(goal, action, objective=objective,
                                  blocked_reason=blocked_reason, priority=priority)
            updated.revision = goal.revision + 1
            updated.updated_at = self.clock()
            goals[index] = updated
            self._save(owner, session_id, goals)
        logger.info(f"| 🎯 Goal {goal_id} {action.value} by {authority.value} "
                    f"→ {updated.phase.value} (rev {updated.revision})")
        return updated

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply(self, goal: Goal, action: GoalAction, *, objective: Optional[str],
               blocked_reason: Optional[str], priority: Optional[TaskPriority]) -> Goal:
        """The phase machine, on a copy. Refuses transitions that mean nothing."""
        next_goal = goal.model_copy(deep=True)
        if not goal.phase.is_open:
            raise GoalStateError(
                f"{goal.id} is complete. A finished goal is history; create a new one "
                f"rather than reopening this."
            )

        if action is GoalAction.EDIT:
            text = (objective or "").strip()
            if not text and priority is None:
                raise GoalStateError("'edit' needs a new objective or a new priority.")
            if text:
                next_goal.objective = text
            if priority is not None:
                next_goal.priority = priority
        elif action is GoalAction.PAUSE:
            if goal.phase is GoalPhase.PAUSED:
                raise GoalStateError(f"{goal.id} is already paused.")
            next_goal.phase = GoalPhase.PAUSED
        elif action is GoalAction.RESUME:
            if goal.phase is GoalPhase.ACTIVE:
                raise GoalStateError(f"{goal.id} is already active.")
            next_goal.phase = GoalPhase.ACTIVE
            next_goal.blocked_reason = None
        elif action is GoalAction.COMPLETE:
            next_goal.phase = GoalPhase.COMPLETE
            next_goal.blocked_reason = None
        elif action is GoalAction.BLOCKED:
            reason = (blocked_reason or "").strip()
            if not reason:
                raise GoalStateError(
                    "'blocked' needs the concrete condition that has to change. "
                    "Difficulty is not blocked; work you can still do is not blocked."
                )
            next_goal.phase = GoalPhase.BLOCKED
            next_goal.blocked_reason = reason
        return next_goal

    def _path(self, owner: str, session_id: str) -> Path:
        return path_manager.get(P.SESSION_GOALS, owner=owner or DEFAULT_OWNER,
                                session_id=session_id or "direct")

    def _load(self, owner: str, session_id: str) -> List[Goal]:
        path = self._path(owner, session_id)
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [Goal.model_validate(item) for item in payload.get("goals", [])]
        except (OSError, ValueError) as error:               # noqa: BLE001
            # Loud, and empty. A half-read goal file must not become a goal:
            # answering "no goal" is recoverable, answering with a wrong
            # objective is not.
            logger.error(f"| ❌ Unreadable goal file {path}: {error}")
            return []

    def _save(self, owner: str, session_id: str, goals: List[Goal]) -> None:
        path = self._path(owner, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"goals": [json.loads(g.model_dump_json()) for g in goals]}
        # Written beside and renamed: a crash mid-write leaves the previous file
        # whole rather than a truncated one that reads as no goal at all.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)


goal_manager = GoalStore()

__all__ = [
    "GoalStore",
    "goal_manager",
    "authority_of",
    "owner_of",
    "session_of",
    "HUMAN_TURN_KEY",
    "OWNER_KEY",
    "DEFAULT_OWNER",
]
