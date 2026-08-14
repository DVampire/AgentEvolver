"""ask_user_question — the agent asks an actual person and waits for the answer.

The sibling of ``escalate_tool``, with a different answerer. Escalation reaches the
parent MetaAgent; this reaches whoever is watching the run. Both suspend on a key
and resume when someone answers it — see ``agentevolver/conversation/question.py``
for the rendezvous and ``agentevolver/runtime/server.py`` for the primitive under it.

The call blocks. That is the point: a decision only a person can make is worth a
step spent waiting, and waiting costs no tokens because nothing is sent while the
question is open. What it does cost is wall-clock, so the tool declares a call
budget larger than the question's own timeout — otherwise the tool manager would
cancel the call out from under a person who was still reading it, and the agent
would see a timeout it could not distinguish from a refusal.
"""

import json
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.conversation.question import DEFAULT_QUESTION_TIMEOUT_S
from agentevolver.conversation.types import UserQuestion
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = ("Ask the user a concise question when you need confirmation, a choice, or "
                "missing information before proceeding.")

_INSTRUCTION = """
## Function
Put a question to the person watching this run and wait for their answer. Use it when continuing would mean guessing at something only they can settle: which of two acceptable designs they want, whether an irreversible step may go ahead, or a fact that exists nowhere in the task or the workspace.

## Parameters
- questions (list, required): one or more question objects. Each has:
  - id (str, required): a stable id you choose; the answer echoes it back so you can match them up.
  - question (str, required): the question itself, in one sentence.
  - header (str, optional): a short heading, e.g. "Confirm" or "Choose mode".
  - detail (str, optional): supporting text shown with the question — not an option.
  - options (list, optional): choices, each `{"label": ..., "description": ...}`. If you recommend one, put it first and append "(Recommended)" to its label.
  - multi_select (bool, optional): whether more than one option may be chosen. Defaults to false.

## Guidance
- Ask sparingly. A question you can answer by reading a file or trying something is not a question for the person.
- Batch related questions into one call rather than interrupting several times.
- Offer options whenever the answer is a choice: a menu is far cheaper for them to answer than free text.
- The call blocks until they answer or the wait expires. The result is JSON: `{"answers": [{"id", "selected", "custom"}]}` — `selected` holds the labels they picked, `custom` any text they typed. An empty `selected` with no `custom` means they passed over that question; treat it as "no preference", not as an error.
- If nobody answers, you are told so. Do not re-ask; make the most reversible choice you can and say in your final result which question went unanswered.

## Example
{"name": "ask_user_question", "args": {"questions": [{"id": "db", "header": "Choose storage", "question": "Which store should the new service use?", "options": [{"label": "SQLite (Recommended)", "description": "No new infrastructure; fine up to a few million rows."}, {"label": "Postgres", "description": "Needs a running server, but scales past that."}]}]}}
"""


@TOOL.register_module(force=True)
class AskUserTool(Tool):
    """Ask the person watching this run a question, and block until they answer."""

    name: str = "ask_user_question"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Asks a person a question; mutates nothing.")
    mutates: bool = Field(default=False, description="Reads a person's intent; changes no state.")
    #: Deliberately longer than ``DEFAULT_QUESTION_TIMEOUT_S``. The inner bound is the
    #: one that should fire, so the agent gets this tool's own "nobody answered"
    #: sentence instead of an opaque cancellation from the tool manager.
    call_timeout_seconds: float = Field(default=DEFAULT_QUESTION_TIMEOUT_S + 300.0)

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, questions: List[Dict[str, Any]], **kwargs) -> Response:
        from agentevolver.conversation.question import question_manager

        ctx = kwargs.get("ctx")
        extra = getattr(ctx, "extra", {}) or {}
        try:
            parsed = [UserQuestion.model_validate(item) for item in (questions or [])]
        except Exception as exc:  # noqa: BLE001 — a malformed question is the model's to fix
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"ask_user_question got a malformed question: {exc}")

        try:
            answers = await question_manager.ask(
                parsed,
                session_id=getattr(ctx, "id", "") or "",
                task_id=str(extra.get("task_id") or getattr(ctx, "subtask_id", "") or ""),
                agent_name=getattr(ctx, "name", "") or "",
            )
        except ValueError as exc:
            return Response(type=ResponseType.TOOL, success=False, message=str(exc))
        except TimeoutError as exc:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"{exc}. Nobody answered. Choose the most reversible option you "
                         f"can, continue, and say in your final result which question "
                         f"went unanswered."),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"| ❌ ask_user_question failed: {exc}")
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Asking the user failed: {exc}")

        payload = {"answers": [answer.model_dump(mode="json") for answer in answers]}
        return Response(type=ResponseType.TOOL, success=True,
                        message=json.dumps(payload, ensure_ascii=False),
                        data=payload)
