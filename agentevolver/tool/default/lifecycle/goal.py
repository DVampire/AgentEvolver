"""The three tools that read and move a session's goal: get, create, update.

The split between them is not convenience, it is authority. Creating a goal and
changing what it asks for take a direct human request; saying where the goal
stands — complete, or blocked on something concrete — is the agent's to report.
The tools do not decide that: they derive the caller's authority from the context
and hand it to the store, which is where the refusal lives.

Nothing here reads an authority argument, and there is deliberately no way to
supply one. An authority the model can name is an authority the model has, and a
goal the agent can quietly rewrite is a note.
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.task.goal import authority_of, goal_manager, owner_of, session_of
from agentevolver.task.types import (
    Goal,
    GoalAction,
    GoalError,
    GoalPhase,
    TaskPriority,
)
from agentevolver.tool.types import Tool

_GET_DESCRIPTION = "Read this session's goal: what it asks for, where it stands, and its current revision."
_GET_GUIDANCE = """
Show the standing objective for this session — the thing the whole session is for, as
opposed to the task in front of you. Returns nothing if no goal has been set.

Call this before every update: an update names the exact revision it read, and a revision
you did not just read may already be stale. If it is, the change you missed is usually the
news.
"""

_GET_EXAMPLES = [
    '{"name": "get_goal_tool", "args": {}}',
]

_CREATE_DESCRIPTION = "Record the standing objective a human has asked for in this session."
_CREATE_GUIDANCE = """
Write down the long-running objective a person actually asked for, so it survives this
conversation and any restart. Use it when someone states something the session is *for* —
not for a single request you can finish now, and not for your own plan of work (that is
plan.md).

- Takes a direct human request. Working autonomously, or as a dispatched sub-agent, you
  cannot create a goal — and that is the point: the objective you are measured against is
  not yours to write.
- Quote the objective in the person's own words as far as you can. It is what "done" will
  later be checked against.
- One open goal at a time. If one is already open, this is refused and names it.
"""

_CREATE_EXAMPLES = [
    '{"name": "create_goal_tool", "args": {"objective": "Get the nightly ETL green and keep it green for a week"}}',
]

_UPDATE_DESCRIPTION = "Report progress on the goal, or apply a change a human asked for."
_UPDATE_GUIDANCE = """
Change one exact revision of the goal. Read it with get_goal_tool first and copy the
goal_id and revision back exactly.

- complete — the objective is actually met. Yours to claim.
- blocked — the same concrete obstacle is stopping the work. Yours to claim; say what has
  to change in blocked_reason. Difficulty is not blocked. Uncertainty is not blocked. Work
  you can still do is not blocked.
- edit, pause, resume — the human's to ask for. Attempted on your own, these are refused,
  and the refusal is not a bug to work around.
"""

_UPDATE_EXAMPLES = [
    '{"name": "update_goal_tool", "args": {"goal_id": "goal_1a2b3c4d", "revision": 3, "action": "complete"}}',
    '{"name": "update_goal_tool", "args": {"goal_id": "goal_1a2b3c4d", "revision": 3, "action": "blocked", "blocked_reason": "The staging database has been unreachable since 14:02; nothing can be verified against it."}}',
]


def _goal_data(goal: Goal) -> Dict[str, Any]:
    """The whole goal as plain data, so a caller need not parse the message."""
    return {
        "goal_id": goal.id,
        "revision": goal.revision,
        "objective": goal.objective,
        "phase": goal.phase.value,
        "priority": goal.priority.name.lower(),
        "blocked_reason": goal.blocked_reason,
        "created_at": goal.created_at.isoformat(),
        "updated_at": goal.updated_at.isoformat(),
    }


def _priority(value: Optional[str]) -> Optional[TaskPriority]:
    """Parse a priority name, or refuse it by raising the store's own error type."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return TaskPriority[str(value).strip().upper()]
    except KeyError:
        raise GoalError(
            f"Unknown priority {value!r}; use low, normal, high or critical."
        ) from None


@TOOL.register_module(force=True)
class GetGoalTool(Tool):
    """Read the session's current goal."""

    name: str = "get_goal_tool"
    description: str = _GET_DESCRIPTION
    guidance: str = _GET_GUIDANCE
    examples: List[str] = _GET_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads the goal; changes nothing.")
    mutates: Optional[bool] = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, **kwargs) -> Response:
        ctx = kwargs.get("ctx")
        goal = goal_manager.current(session_of(ctx), owner=owner_of(ctx))
        if goal is None:
            # Said plainly rather than returned as emptiness: "no goal" is a real
            # answer, and an empty result reads as a tool that failed.
            return Response(type=ResponseType.TOOL, success=True,
                            message="No goal has been set for this session.",
                            data={"goal": None})
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"{goal.summary()}\n"
                     f"Copy goal_id={goal.id} and revision={goal.revision} into any update."),
            data={"goal": _goal_data(goal)},
        )


@TOOL.register_module(force=True)
class CreateGoalTool(Tool):
    """Record a goal a human asked for."""

    name: str = "create_goal_tool"
    description: str = _CREATE_DESCRIPTION
    guidance: str = _CREATE_GUIDANCE
    examples: List[str] = _CREATE_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Writes the session's goal; human authority required.")
    mutates: Optional[bool] = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, objective: str, priority: Optional[str] = None, **kwargs) -> Response:
        """Record what the person asked for as the run's goal.

        Args:
            objective: What the person asked for.
            priority: low | normal | high | critical. Defaults to normal.
        """
        ctx = kwargs.get("ctx")
        try:
            goal = goal_manager.create(
                session_id=session_of(ctx),
                owner=owner_of(ctx),
                objective=objective,
                authority=authority_of(ctx),
                priority=_priority(priority) or TaskPriority.NORMAL,
            )
        except GoalError as error:
            logger.info(f"| 🎯 create_goal_tool refused: {error}")
            return Response(type=ResponseType.TOOL, success=False, message=str(error))
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"Goal set: {goal.summary()}",
            data={"goal": _goal_data(goal)},
        )


@TOOL.register_module(force=True)
class UpdateGoalTool(Tool):
    """Move the goal — progress the agent may report, changes only a human may ask for."""

    name: str = "update_goal_tool"
    description: str = _UPDATE_DESCRIPTION
    guidance: str = _UPDATE_GUIDANCE
    examples: List[str] = _UPDATE_EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Writes the session's goal.")
    mutates: Optional[bool] = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, goal_id: str, revision: int, action: str,
                       objective: Optional[str] = None,
                       blocked_reason: Optional[str] = None,
                       priority: Optional[str] = None, **kwargs) -> Response:
        """Advance, edit, or close the run's goal.

        Args:
            goal_id: From ``get_goal_tool``.
            revision: From ``get_goal_tool``. A mismatch means it changed under you.
            action: complete | blocked | edit | pause | resume.
            objective: The replacement text; only for ``edit``.
            blocked_reason: Required for ``blocked`` — the concrete condition.
            priority: low | normal | high | critical; only for ``edit``.
        """
        ctx = kwargs.get("ctx")
        try:
            chosen = GoalAction(str(action).strip().lower())
        except ValueError:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"Unknown action {action!r}. Use one of: "
                         f"{', '.join(a.value for a in GoalAction)}."),
            )

        try:
            goal = goal_manager.update(
                session_id=session_of(ctx),
                owner=owner_of(ctx),
                goal_id=str(goal_id).strip(),
                revision=int(revision),
                action=chosen,
                authority=authority_of(ctx),
                objective=objective,
                blocked_reason=blocked_reason,
                priority=_priority(priority),
            )
        except GoalError as error:
            logger.info(f"| 🎯 update_goal_tool refused ({chosen.value}): {error}")
            return Response(type=ResponseType.TOOL, success=False, message=str(error))
        except (TypeError, ValueError) as error:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"revision must be the integer from get_goal_tool: {error}")

        closing = ""
        if goal.phase is GoalPhase.COMPLETE:
            closing = " The goal is closed; a new one takes a human request."
        elif goal.phase is GoalPhase.BLOCKED:
            closing = " Say so in your answer — a blocked goal needs the person to act."
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{goal.summary()}.{closing}",
            data={"goal": _goal_data(goal)},
        )
