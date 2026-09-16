"""Use the existing human-question channel for one-shot CLI action approvals."""
from __future__ import annotations

import hashlib

from agentevolver.conversation.question import question_manager
from agentevolver.conversation.types import QuestionOption, UserQuestion


async def ask_action_approval(execution, reason: str) -> bool:
    """Approve only an explicit answer for this immutable execution; timeout denies."""
    question = UserQuestion(
        header="Action approval",
        question=reason or f"Allow one call to {execution.tool_name}?",
        detail=(f"Action: {execution.tool_name}@{execution.tool_version}\n"
                f"Call: {execution.call_id}\n"
                f"Argument fields: {', '.join(sorted(execution.arguments))}\n"
                f"Arguments SHA-256: {hashlib.sha256(execution.arguments_json.encode()).hexdigest()}"),
        options=[QuestionOption(label="Deny"), QuestionOption(label="Allow once")],
    )
    answers = await question_manager.ask(
        [question], session_id=execution.session_id, task_id=execution.task_id,
        agent_name=execution.agent_name, timeout=300,
    )
    return any(a.id == question.id and a.selected == ["Allow once"] and not a.custom
               for a in answers)
