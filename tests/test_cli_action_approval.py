from types import SimpleNamespace

import pytest

from agentevolver.conversation.approval import ask_action_approval
from agentevolver.conversation.types import UserAnswer


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,custom,allowed", [
    (["Allow once"], "", True), (["Deny"], "", False),
    ([], "yes", False), (["Allow once"], "actually no", False),
])
async def test_cli_approval_needs_exact_explicit_answer(monkeypatch, selected, custom, allowed):
    from agentevolver.conversation.approval import question_manager
    call = SimpleNamespace(tool_name="source__update", tool_version="1.0.0", call_id="call-1",
                           arguments={"token": "secret"}, arguments_json='{"token":"secret"}',
                           session_id="session", task_id="task", agent_name="researcher")

    async def ask(questions, **kwargs):
        q = questions[0]
        assert kwargs["session_id"] == "session" and kwargs["timeout"] == 300
        assert "secret" not in q.detail and "call-1" in q.detail
        assert q.options[0].label == "Deny"
        return [UserAnswer(id=q.id, selected=selected, custom=custom)]

    monkeypatch.setattr(question_manager, "ask", ask)
    assert await ask_action_approval(call, "Change external state?") is allowed
