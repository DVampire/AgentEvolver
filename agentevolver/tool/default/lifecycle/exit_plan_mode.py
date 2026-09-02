"""exit_plan_mode — present the plan, and leave plan mode only if a person approves.

The one way out of the gate in ``agentevolver/hook/default/plan_mode.py``. It puts
the plan to a person through ``ask_user_question``'s machinery — the same pending
question, the same trace announcement, the same suspend/resume rendezvous — tagged
with a ``plan-review`` intent so a UI that knows the tag can render a plan review
instead of a two-item menu. A UI that does not know the tag shows the menu, and the
answer read here is identical either way.

Declining is a *failed* call, not a successful one reporting a refusal. The
difference matters to the model: a successful call reads as the step having worked,
and an agent that reads its plan's rejection as progress goes on to act on it.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.conversation.types import (
    QuestionIntent,
    QuestionOption,
    UserQuestion,
)
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

#: The label that approves. Named in the intent as well, so no UI has to infer the
#: verdict from a position in the option list.
APPROVE_LABEL = "Approve and start"
DECLINE_LABEL = "Keep planning"

_DESCRIPTION = "Present your finished plan for approval and leave plan mode if the user approves it."

_GUIDANCE = """
Show the person watching this run the plan you intend to carry out, and wait for their verdict. Approval is what lifts plan mode: until they give it, every action that changes anything is refused.

- Call this once you know what you mean to do — not to check in mid-exploration. Reading, searching and reasoning are never blocked, so there is no reason to exit early.
- Write the plan for someone who has not read the code: name the files, the steps, and anything you had to guess. The point of the review is that they can disagree with an assumption before it costs anything.
- If they decline, the call fails and you stay in plan mode. Read their reply, revise, and present a new plan — do not re-send the same one.
- Only works while plan mode is active; outside it the call fails and nothing changes.
"""

_EXAMPLES = [
    '{"name": "exit_plan_mode", "args": {"plan": "1. Add `retry_after` to `HttpRequestTool` (tool/default/data_sources.py).\\\\n2. Honour it in the 429 branch.\\\\n3. Add a test that a 429 with `Retry-After: 2` waits.\\\\n\\\\nAssumption: only the 429 path needs it; 503 is already retried."}}',
]


@TOOL.register_module(force=True)
class ExitPlanModeTool(Tool):
    """Put the plan to a person and leave plan mode only on an explicit approval."""

    name: str = "exit_plan_mode"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Asks for approval; changes nothing but the gate.")
    mutates: bool = Field(default=False, description="Flips a per-run flag; touches no workspace state.")
    #: Matches ``ask_user_question``: the wait is a person reading a plan, and the
    #: question's own timeout is the bound that should fire.
    call_timeout_seconds: float = Field(default=3900.0)

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, plan: str, **kwargs) -> Response:
        """Present the finished plan for approval.

        Args:
            plan: The complete plan, in markdown. What you will change, in what order,
                and what you are assuming.
        """
        from agentevolver.conversation.question import question_manager
        from agentevolver.plan.server import plan_manager

        ctx = kwargs.get("ctx")
        extra = getattr(ctx, "extra", {}) or {}
        session_id = getattr(ctx, "id", "") or ""

        if not (plan or "").strip():
            return Response(type=ResponseType.TOOL, success=False,
                            message="exit_plan_mode needs the plan itself; `plan` was empty.")
        if not plan_manager.active(session_id):
            return Response(
                type=ResponseType.TOOL, success=False,
                message=("This run is not in plan mode, so there is nothing to exit. "
                         "Carry on with the task."),
            )

        question = UserQuestion(
            id="plan-review",
            header="Review plan",
            question="Approve this plan and let the agent start?",
            detail=plan,
            options=[
                QuestionOption(label=APPROVE_LABEL,
                               description="Lift plan mode and carry out the plan as written."),
                QuestionOption(label=DECLINE_LABEL,
                               description="Stay in plan mode; say what should change."),
            ],
            intent=QuestionIntent(type="plan-review", approve=APPROVE_LABEL),
        )

        try:
            answers = await question_manager.ask(
                [question],
                session_id=session_id,
                task_id=str(extra.get("task_id") or getattr(ctx, "subtask_id", "") or ""),
                agent_name=getattr(ctx, "name", "") or "",
            )
        except TimeoutError as exc:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"{exc}. Nobody reviewed the plan, so plan mode stays on. Wait "
                         f"for a reply rather than re-sending the same plan."),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"| ❌ exit_plan_mode review failed: {exc}")
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"The plan review could not be opened: {exc}. Plan mode stays on.")

        answer = next((a for a in answers if a.id == question.id), None)
        approved = answer is not None and APPROVE_LABEL in answer.selected
        if not approved:
            feedback = (answer.custom if answer is not None else "") or "no reason given"
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"The plan was not approved: {feedback}. You are still in plan "
                         f"mode. Revise the plan against that reply and present the new "
                         f"one with `exit_plan_mode`."),
            )

        plan_manager.approve(session_id, plan)
        note = (answer.custom or "").strip()
        return Response(
            type=ResponseType.TOOL, success=True,
            message=("Plan approved. Plan mode is off and you may now take actions that "
                     "change state. Carry out the plan as approved."
                     + (f" The reviewer added: {note}" if note else "")),
            data={"approved": True, "plan": plan, "note": note},
        )
