"""Ask-question tool — put a decision/clarification question to the authority above.

Distinct from `escalate_tool`: escalate is "I'm blocked, give me guidance"; this is
"here is a decision that is not mine to make — pick one." It carries a structured
question, candidate options, and a default, and is used even when not blocked.

In this autonomous framework there is no live human wired to a sub-agent, so the
question is routed to the parent MetaAgent (via `escalation_hook`) — the decision
authority that stands in for the user — and its reply is returned as the answer.
Running standalone (no parent), the tool cannot reach anyone: it returns the
supplied `default` if given, otherwise reports that no one is available so the
caller proceeds on its own best judgement.
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.logger import logger
from src.registry import TOOL

_DESCRIPTION = "Ask a decision/clarification question (optionally with options) and get an answer; routed to the parent MetaAgent."

_INSTRUCTION = """
## Function
Ask a question when a decision is genuinely not yours to make — an ambiguous requirement, a choice between reasonable options, or a preference you cannot infer from the task. The question is routed to the parent MetaAgent (the decision authority, standing in for the user) and the call blocks until it replies; the reply is returned as the answer.

This is NOT `escalate_tool`. Use `escalate_tool` when you are blocked and need guidance to get unstuck; use this when you are not blocked but need someone to pick a direction or clarify intent.

## Parameters
- question (str, required): the single, focused question to ask.
- options (list[str], optional): candidate answers to choose from. Omit for a free-form question.
- header (str, optional): a short topic label for the question (e.g. "Auth method").
- multi_select (bool, optional, default false): allow more than one option to be chosen.
- context (str, optional): background that helps answer (what you tried, why it's ambiguous).
- default (str, optional): the answer to assume if no one is reachable (standalone / non-interactive). Provide this so the flow can continue autonomously.

## Guidance
- Reserve for decisions that must be made above you; do NOT ask routine questions you can answer yourself — that just blocks the run.
- Only reaches someone when you were dispatched by a MetaAgent. Standalone, it returns `default` (if given) or reports no one is available; then proceed with your best judgement.
- Ask ONE focused question per call. Attach `options` and a `default` whenever you can so the answer is fast and the autonomous fallback is safe.

## Example
{"name": "ask_question_tool", "args": {"question": "Which database should the service use?", "header": "Database", "options": ["PostgreSQL", "SQLite", "MongoDB"], "default": "PostgreSQL", "context": "The spec is silent on storage; the data is relational."}}
"""


@TOOL.register_module(force=True)
class AskQuestionTool(Tool):
    """Ask a decision/clarification question (via the parent MetaAgent) and return the answer."""

    name: str = "ask_question_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Only asks for an answer; mutates nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    @staticmethod
    def _format(options: Optional[List[str]], multi_select: bool, context: str) -> str:
        parts = []
        if context:
            parts.append(context.strip())
        if options:
            how = "select all that apply" if multi_select else "select one"
            opts = "\n".join(f"  - {o}" for o in options)
            parts.append(f"Options ({how}):\n{opts}")
        return "\n\n".join(parts)

    async def __call__(
        self,
        question: str,
        options: Optional[List[str]] = None,
        header: str = "",
        multi_select: bool = False,
        context: str = "",
        default: str = "",
        **kwargs,
    ) -> Response:
        if not question or not str(question).strip():
            return Response(type=ResponseType.TOOL, success=False, message="`question` is required.")
        question = str(question).strip()
        situation = self._format(options, multi_select, context)

        ctx = kwargs.get("ctx")
        parent_session_id = getattr(ctx, "parent_session_id", None)
        if not parent_session_id:
            # No one reachable (standalone / top-level). Fall back to the default.
            if default:
                logger.info("| ❓ ask_question_tool: no parent — using provided default")
                return Response(
                    type=ResponseType.TOOL, success=True,
                    message=default,
                    data={"answered_by": "default", "question": question, "answer": default},
                )
            return Response(
                type=ResponseType.TOOL, success=False,
                message="No decision authority is available (running standalone). Proceed with your own best judgement.",
                data={"answered_by": "none", "question": question},
            )

        header_txt = f"[{header}] " if header else ""
        try:
            from src.protocol import protocol_manager
            answer = await protocol_manager.escalate(
                ctx, reason=f"{header_txt}{question}", situation=situation, suggestion=default,
            )
            if not answer:
                answer = default or "No answer returned; use your best judgement."
            logger.info(f"| ❓ ask_question_tool: asked parent → answer received")
            return Response(
                type=ResponseType.TOOL, success=True,
                message=answer,
                data={"answered_by": "meta", "question": question, "answer": answer},
            )
        except Exception as e:
            logger.error(f"| ❌ ask_question_tool failed: {e}")
            if default:
                return Response(type=ResponseType.TOOL, success=True, message=default,
                                data={"answered_by": "default", "question": question, "answer": default})
            return Response(type=ResponseType.TOOL, success=False, message=f"Ask-question failed: {e}")
