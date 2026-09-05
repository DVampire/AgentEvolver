"""Provider-bound accounting preserves messages, including on overflow."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentevolver.message import AssistantMessage, HumanMessage, ToolMessage
from agentevolver.model.context import ModelContextManager
from agentevolver.model.pressure import (
    ESTIMATE_METHOD,
    RequestTokenEstimator,
    RequestTokenEstimatorRegistrationError,
    estimate_tokens,
    prepare_messages,
    register_request_token_estimator,
    resolve_request_token_estimator,
)
from agentevolver.model.types import ModelConfig, ModelContext
from agentevolver.response import Response, ResponseType


def test_pressure_below_the_threshold_leaves_messages_byte_for_byte_unchanged():
    messages = [
        HumanMessage(content="question"),
        ToolMessage(
            content="small result",
            tool_call_id="call-1",
            name="read_file_tool",
        ),
    ]

    prepared = prepare_messages(
        messages,
        context_window=10_000,
        reserved_output_tokens=1_000,
    )

    assert prepared.messages == messages
    assert prepared.pressure["triggered"] is False
    assert prepared.pressure["pruned_message_indices"] == []


def test_pressure_preserves_even_oversized_tool_results():
    old_result = "HEAD-" + ("x" * 18_000) + "-TAIL"
    recent_result = "recent observation"
    messages = [
        HumanMessage(content="do not rewrite this instruction"),
        AssistantMessage(content="", tool_calls=[]),
        ToolMessage(content=old_result, tool_call_id="call-old", name="bash_tool"),
        ToolMessage(content=recent_result, tool_call_id="call-new", name="read_file_tool"),
    ]

    first = prepare_messages(
        messages,
        context_window=2_500,
        reserved_output_tokens=500,
    )
    second = prepare_messages(
        messages,
        context_window=2_500,
        reserved_output_tokens=500,
    )

    assert first.messages == second.messages
    assert first.pressure == second.pressure
    assert first.pressure["estimate_method"] == ESTIMATE_METHOD
    assert first.pressure["pruned_message_indices"] == []
    assert first.pressure["removed_chars"] == 0
    assert first.pressure["over_capacity"] is True
    assert first.messages[0] == messages[0]
    assert first.messages[3].content == recent_result
    assert first.messages[2].content == old_result
    assert messages[2].content == old_result  # append-only history was not changed


@pytest.mark.asyncio
async def test_model_dispatch_and_snapshot_receive_the_same_complete_request():
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
        model_name="main",
        model_type="chat/completions",
        model_id="provider/model",
        provider="provider",
        max_completion_tokens=500,
        context_window=6_000,
    )
    manager.model_clients["main"] = Client()
    original = ToolMessage(
        content="begin-" + ("z" * 18_000) + "-end",
        tool_call_id="call-1",
        name="bash_tool",
    )

    with patch("agentevolver.model.context._record_request_snapshot", side_effect=record):
        result = await manager(
            name="main",
            input={"messages": [HumanMessage(content="question"), original], "max_retries": 1},
            ctx=ModelContext(id="pressure-session"),
        )

    assert result.success
    assert dispatched[0] == recorded[0]["messages"]
    assert recorded[0]["pressure"]["text_policy"] == "preserve"
    assert dispatched[0][1].content == original.content
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


def test_pressure_records_validated_four_layer_token_accounting():
    from agentevolver.agent.context import ContextEnvelope
    from agentevolver.message import AssistantMessage, HumanMessage, SystemMessage

    messages = ContextEnvelope(
        fixed=(SystemMessage(content="rules"), HumanMessage(content="task")),
        recent=(AssistantMessage(content="worked"),),
        live=(HumanMessage(content="continue"),),
    ).flatten()

    prepared = prepare_messages(messages, context_window=100_000)
    layers = prepared.pressure["context_layers"]

    assert list(layers) == ["fixed", "checkpoint", "recent", "live"]
    assert layers["fixed"]["messages"] == 2
    assert layers["checkpoint"] == {"messages": 0, "tokens": 0}
    assert layers["recent"]["tokens"] > 0
    assert layers["live"]["tokens"] > 0


def test_unknown_provider_uses_documented_deterministic_fallback():
    assert resolve_request_token_estimator(provider="anthropic", model="claude") is None


def test_image_estimate_depends_on_pixels_not_png_compression():
    import base64
    import io
    from PIL import Image
    from agentevolver.message.types import ContentPartImage, ImageURL

    messages = []
    for compression in (0, 9):
        buffer = io.BytesIO()
        Image.new("RGB", (1280, 900), "green").save(buffer, format="PNG", compress_level=compression)
        url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        messages.append(HumanMessage(content=[ContentPartImage(image_url=ImageURL(url=url))]))
    original = [message.model_dump() for message in messages]
    assert estimate_tokens(messages[0]) == estimate_tokens(messages[1])
    assert 2000 < estimate_tokens(messages[0]) < 5000
    assert [message.model_dump() for message in messages] == original
    # A data URI inside actual text is still text. Do not censor arbitrary strings.
    assert estimate_tokens(HumanMessage(content=messages[0].content[0].image_url.url)) > 100_000


@pytest.mark.parametrize("part", [
    {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
    {"type": "input_image", "image_url": "data:image/png;base64,bad"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "bad"}},
])
def test_visual_budget_is_explicit_even_with_a_text_tokenizer(part):
    counted = []
    estimator = RequestTokenEstimator(
        count_text=lambda value: counted.append(value) or 100,
        method="test", tokenizer_exact=True, provider_wire_exact=True,
    )
    prepared = prepare_messages([{"role": "user", "content": [part]}], token_estimator=estimator)
    assert prepared.pressure["image_count"] == 1
    assert prepared.pressure["image_tokens_estimated"] == 4096
    assert prepared.pressure["estimated_tokens_after"] == 4196
    assert prepared.pressure["provider_wire_exact"] is False
    assert "base64" not in counted[0] and "example.invalid" not in counted[0]
    assert prepared.messages[0]["content"][0] == part


def test_registered_visual_counter_overrides_portable_budget():
    estimator = RequestTokenEstimator(lambda text: 100, "test", True, count_image=lambda part: 300)
    prepared = prepare_messages(
        [{"role": "user", "content": [{"type": "input_image", "file_id": "file-1"}]}],
        token_estimator=estimator,
    )
    assert prepared.pressure["estimated_tokens_after"] == 400
    assert prepared.pressure["image_estimate_method"] == "registered"


def test_visual_accounting_accepts_union_types_in_tool_schemas():
    tool = {"type": "function", "parameters": {"type": ["object", "null"]}}
    prepared = prepare_messages([HumanMessage(content="complete text")], tools=[tool])
    assert prepared.pressure["image_count"] == 0
    assert prepared.messages[0].content == "complete text"


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
        provider="anthropic",
        estimator=wildcard,
    )
    remove_exact = register_request_token_estimator(
        provider="anthropic",
        model="claude-special",
        estimator=exact,
    )
    try:
        assert (
            resolve_request_token_estimator(
                provider="Anthropic",
                model="claude-other",
            )
            is wildcard
        )
        assert (
            resolve_request_token_estimator(
                provider="anthropic",
                model="CLAUDE-SPECIAL",
            )
            is exact
        )
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
        provider="private",
        estimator=new,
        replace=True,
    )
    try:
        remove_old()
        assert resolve_request_token_estimator(provider="private", model="m") is new
    finally:
        remove_new()


# --------------------------------------------------------------------------- #
# When pruning cannot make it fit
# --------------------------------------------------------------------------- #
# Only tool results may be reduced at this boundary; a history that is mostly
# instructions and reasoning can exceed the window with nothing left to shrink. That
# fact was computed and recorded as `unresolved` from the start — and read by nobody,
# so the oversized request went out anyway and came back as a provider error.


def _unprunable(chars: int = 40_000):
    """A history too large to send and with nothing this layer is allowed to shrink."""
    return [HumanMessage(content="q" * chars), AssistantMessage(content="a" * chars)]


def _manager(window: int = 2_500, *, fallback: str = "") -> ModelContextManager:
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_type="chat/completions",
        model_id="provider/model",
        provider="provider",
        max_completion_tokens=500,
        context_window=window,
        fallback_model=fallback,
    )
    return manager


class _Client:
    """Records what it was asked to send, so 'never sent' is observable.

    ``rejects_over`` makes it behave the way a real endpoint does with an oversized
    request: a plain exception, indistinguishable to the retry policy from a rate limit.
    A stub that accepts anything would let a test about not retrying pass without the
    code that prevents it.
    """

    def __init__(self, reply: str = "answer", rejects_over: int = 0):
        self.calls: list = []
        self.reply = reply
        self.rejects_over = rejects_over

    async def __call__(self, **kwargs):
        self.calls.append(kwargs["messages"])
        if self.rejects_over and estimate_tokens(kwargs["messages"]) > self.rejects_over:
            raise RuntimeError("maximum context length exceeded")
        return Response(type=ResponseType.LLM, success=True, message=self.reply)

    def set_api_key(self, _key):
        pass


def test_a_request_that_still_does_not_fit_is_marked_over_capacity():
    """`unresolved` cannot carry this: a request above the trigger may still fit.

    The two answer different questions — "did pruning reach its target" and "can this be
    sent at all" — and only the second one has an action attached to it.
    """
    prepared = prepare_messages(
        _unprunable(),
        context_window=2_500,
        reserved_output_tokens=500,
    )
    assert prepared.pressure["over_capacity"] is True
    assert prepared.pressure["estimated_tokens_after"] > prepared.pressure["input_capacity_tokens"]


def test_a_large_request_that_does_fit_is_not_marked_over_capacity():
    """The guard has to stay narrow. Refusing a request merely for being large would
    turn a working long conversation into a failure."""
    prepared = prepare_messages(
        [HumanMessage(content="q" * 100)],
        context_window=2_500,
        reserved_output_tokens=500,
    )
    assert prepared.pressure["over_capacity"] is False


@pytest.mark.asyncio
async def test_an_unsendable_request_is_never_dispatched():
    """It was being sent, rejected, and reported as though the provider had failed."""
    manager = _manager()
    manager.model_clients["main"] = client = _Client()

    result = await manager(
        name="main",
        input={"messages": _unprunable(), "max_retries": 3},
        ctx=ModelContext(id="overflow-session"),
    )

    assert client.calls == [], "an oversized request reached the provider"
    assert result.success is False


@pytest.mark.asyncio
async def test_an_unsendable_request_does_not_spend_its_retries():
    """The cost of treating this as an ordinary failure.

    Every attempt sends the identical request and is rejected identically, so a retry
    policy spends its whole budget — and its backoff — on an outcome that was decided
    before the first call.
    """
    manager = _manager()
    manager.model_clients["main"] = _Client(rejects_over=2_000)
    retries = []

    async def record(**kwargs):
        retries.append(kwargs)

    with patch("agentevolver.model.context._record_retry", side_effect=record):
        await manager(
            name="main",
            input={"messages": _unprunable(), "max_retries": 3},
            ctx=ModelContext(id="overflow-session"),
        )

    assert retries == [], f"retried an unsendable request {len(retries)} times"


@pytest.mark.asyncio
async def test_a_context_that_will_not_fit_one_model_still_tries_a_larger_one():
    """Windows differ, which is part of why a fallback exists.

    Ending the whole call here would make the guard worse than the bug: the fallback
    could have taken the request unchanged.
    """
    manager = _manager(fallback="roomy")
    manager.models["roomy"] = ModelConfig(
        model_name="roomy",
        model_type="chat/completions",
        model_id="provider/roomy",
        provider="provider",
        max_completion_tokens=500,
        context_window=200_000,
    )
    manager.model_clients["main"] = small = _Client()
    manager.model_clients["roomy"] = large = _Client(reply="from the larger model")

    result = await manager(
        name="main",
        input={"messages": _unprunable(), "max_retries": 3},
        ctx=ModelContext(id="overflow-session"),
    )

    assert small.calls == []
    assert len(large.calls) == 1
    assert result.success and result.message == "from the larger model"


@pytest.mark.asyncio
async def test_the_refusal_says_what_has_to_happen_next():
    """ "Provider rejected the request" sends a reader to the provider's status page.

    What is actually true is that the conversation has outgrown the window and only
    compaction, one level up, can reduce what is left — this boundary may not touch
    instructions or reasoning.
    """
    manager = _manager()
    manager.model_clients["main"] = _Client()

    result = await manager(
        name="main",
        input={"messages": _unprunable(), "max_retries": 1},
        ctx=ModelContext(id="overflow-session"),
    )

    assert result.success is False
    assert "does not fit main" in result.message
    assert "compact complete conversation turns" in result.message
