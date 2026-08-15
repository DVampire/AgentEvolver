"""Provider-bound pruning is deterministic, narrow, and recorded."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentevolver.message import AssistantMessage, HumanMessage, ToolMessage
from agentevolver.model.context import ModelContextManager
from agentevolver.model.pressure import ESTIMATE_METHOD, prepare_messages
from agentevolver.model.pressure import (
    RequestTokenEstimator,
    RequestTokenEstimatorRegistrationError,
    register_request_token_estimator,
    resolve_request_token_estimator,
)
from agentevolver.model.types import ModelConfig, ModelContext
from agentevolver.response import Response, ResponseType


def test_pressure_below_the_threshold_leaves_messages_byte_for_byte_unchanged():
    messages = [HumanMessage(content="question"), ToolMessage(
        content="small result", tool_call_id="call-1", name="read_file_tool",
    )]

    prepared = prepare_messages(
        messages, context_window=10_000, reserved_output_tokens=1_000,
    )

    assert prepared.messages == messages
    assert prepared.pressure["triggered"] is False
    assert prepared.pressure["pruned_message_indices"] == []


def test_pressure_prunes_oldest_tool_results_without_mutating_the_history():
    old_result = "HEAD-" + ("x" * 18_000) + "-TAIL"
    recent_result = "recent observation"
    messages = [
        HumanMessage(content="do not rewrite this instruction"),
        AssistantMessage(content="", tool_calls=[]),
        ToolMessage(content=old_result, tool_call_id="call-old", name="bash_tool"),
        ToolMessage(content=recent_result, tool_call_id="call-new", name="read_file_tool"),
    ]

    first = prepare_messages(
        messages, context_window=2_500, reserved_output_tokens=500,
    )
    second = prepare_messages(
        messages, context_window=2_500, reserved_output_tokens=500,
    )

    assert first.messages == second.messages
    assert first.pressure == second.pressure
    assert first.pressure["estimate_method"] == ESTIMATE_METHOD
    assert first.pressure["pruned_message_indices"] == [2]
    assert first.messages[0] == messages[0]
    assert first.messages[3].content == recent_result
    assert "HEAD-" in first.messages[2].content and "-TAIL" in first.messages[2].content
    assert "complete tool result remains in Trace" in first.messages[2].content
    assert messages[2].content == old_result  # append-only history was not changed


@pytest.mark.asyncio
async def test_model_dispatch_and_snapshot_receive_the_same_pruned_request():
    dispatched = []
    recorded = []

    class Client:
        async def __call__(self, **kwargs):
            dispatched.append(kwargs["messages"])
            return Response(type=ResponseType.LLM, success=True, message="answer")

        def set_api_key(self, _key):
            pass

    async def record(**kwargs):
        recorded.append(kwargs)

    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main", model_type="chat/completions", model_id="provider/model",
        provider="provider", max_completion_tokens=500, context_window=2_500,
    )
    manager.model_clients["main"] = Client()
    original = ToolMessage(
        content="begin-" + ("z" * 18_000) + "-end",
        tool_call_id="call-1", name="bash_tool",
    )

    with patch("agentevolver.model.context._record_request_snapshot", side_effect=record):
        result = await manager(
            name="main",
            input={"messages": [HumanMessage(content="question"), original], "max_retries": 1},
            ctx=ModelContext(id="pressure-session"),
        )

    assert result.success
    assert dispatched[0] == recorded[0]["messages"]
    assert recorded[0]["pressure"]["triggered"] is True
    assert len(dispatched[0][1].content) < len(original.content)
    assert len(original.content) == 18_010


def test_openai_native_tokenizer_is_used_without_claiming_wire_exactness():
    estimator = resolve_request_token_estimator(provider="openai", model="gpt-4o")
    assert estimator is not None

    prepared = prepare_messages(
        [HumanMessage(content="hello")],
        context_window=10_000,
        reserved_output_tokens=1_000,
        token_estimator=estimator,
    )

    assert prepared.pressure["estimate_method"].startswith("tiktoken:")
    assert prepared.pressure["tokenizer_exact"] is True
    assert prepared.pressure["provider_wire_exact"] is False


def test_unknown_provider_uses_documented_deterministic_fallback():
    assert resolve_request_token_estimator(provider="anthropic", model="claude") is None


def test_deployment_can_register_exact_or_provider_wide_token_estimator():
    wildcard = RequestTokenEstimator(
        count_text=lambda text: 17,
        method="gateway-cache:anthropic",
        tokenizer_exact=True,
        provider_wire_exact=True,
    )
    exact = RequestTokenEstimator(
        count_text=lambda text: 23,
        method="gateway-cache:claude-special",
        tokenizer_exact=True,
        provider_wire_exact=True,
    )
    remove_wildcard = register_request_token_estimator(
        provider="anthropic", estimator=wildcard,
    )
    remove_exact = register_request_token_estimator(
        provider="anthropic", model="claude-special", estimator=exact,
    )
    try:
        assert resolve_request_token_estimator(
            provider="Anthropic", model="claude-other",
        ) is wildcard
        assert resolve_request_token_estimator(
            provider="anthropic", model="CLAUDE-SPECIAL",
        ) is exact
        with pytest.raises(RequestTokenEstimatorRegistrationError, match="already registered"):
            register_request_token_estimator(provider="anthropic", estimator=wildcard)
    finally:
        remove_exact()
        remove_wildcard()


def test_old_estimator_disposer_cannot_remove_its_replacement():
    old = RequestTokenEstimator(lambda text: 1, "old", False)
    new = RequestTokenEstimator(lambda text: 2, "new", False)
    remove_old = register_request_token_estimator(provider="private", estimator=old)
    remove_new = register_request_token_estimator(
        provider="private", estimator=new, replace=True,
    )
    try:
        remove_old()
        assert resolve_request_token_estimator(provider="private", model="m") is new
    finally:
        remove_new()
