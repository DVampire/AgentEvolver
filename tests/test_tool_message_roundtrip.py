"""Every provider must be able to replay a tool result.

`derive_context` projects the log into real turns — `[user, assistant(+tool_calls),
tool, ...]` — and the `tool` turn is a `ToolMessage`. The rendered path never produced
one: it folds results into prose inside a single user message. So every serializer could
reject the type and nothing noticed, until a run with the switch on failed on every step
after the first with `Unknown message type: ToolMessage`.

It stayed hidden because the failure was silent twice over: `_think` returns its error
inside the decision rather than raising, and the trace hook stamped `success=True` on
every agent_end regardless. The run was measured, tabulated, and reported as working.

No provider takes the same shape, which is the point of testing all of them:
chat/completions has a `tool` role, Responses has `function_call_output`, Anthropic has
a `tool_result` block on a user turn, Gemini has `function_response` paired by *name*.
"""

import pytest

from agentevolver.message.types import ToolMessage

RESULT = ToolMessage(content="wrote 2 files", tool_call_id="call_1",
                     name="write_file_tool")


def _serializer(module, cls):
    return getattr(__import__(f"agentevolver.model.{module}.serializer",
                              fromlist=[cls]), cls)


CHAT = [("llm_hub", "LLMHubChatSerializer", "serialize_message"),
        ("openrouter", "OpenRouterChatSerializer", "serialize_message"),
        ("openai", "OpenAIChatSerializer", "serialize")]


@pytest.mark.parametrize("module,cls,fn", CHAT)
def test_chat_providers_use_the_tool_role(module, cls, fn):
    out = getattr(_serializer(module, cls), fn)(RESULT)
    assert out["role"] == "tool"
    assert out["tool_call_id"] == "call_1"
    assert out["content"] == "wrote 2 files"


def test_the_responses_api_echoes_the_call_id_as_an_item():
    """Not a `tool` role — that surface has none — and `call_id`, not the item's `id`."""
    out = _serializer("openai", "OpenAIResponseSerializer").serialize(RESULT)
    assert out == {"type": "function_call_output", "call_id": "call_1",
                   "output": "wrote 2 files"}


def test_anthropic_carries_the_result_on_a_user_turn():
    out = _serializer("anthropic", "AnthropicChatSerializer").serialize(RESULT)
    assert out["role"] == "user"
    block = out["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_1"


def test_anthropic_marks_a_failed_call_rather_than_describing_it():
    """`is_error` stays structured so a failure is not prose the model must interpret."""
    failed = ToolMessage(content="boom", tool_call_id="call_1", name="t", is_error=True)
    block = _serializer("anthropic", "AnthropicChatSerializer").serialize(failed)["content"][0]
    assert block["is_error"] is True


def test_gemini_pairs_by_name_not_by_id():
    """The reason `ToolMessage` carries a name at all.

    Gemini matches a `function_response` to its call by the declared function name. A
    result without one cannot be replayed there — pairing by position would survive
    only until compaction folded a range away.
    """
    out = _serializer("google", "GoogleChatSerializer").serialize(RESULT)
    part = out["parts"][0]["function_response"]
    assert part["name"] == "write_file_tool"
    assert part["response"] == {"result": "wrote 2 files"}


def test_a_derived_history_serializes_end_to_end_on_every_provider():
    """The actual failing shape: what `derive_messages` builds, through each serializer."""
    from agentevolver.message.types import AssistantMessage, HumanMessage

    history = [HumanMessage(content="reverse a string"),
               AssistantMessage(content="I'll write the files."),
               RESULT]

    for module, cls, fn in CHAT + [("anthropic", "AnthropicChatSerializer", "serialize"),
                                   ("google", "GoogleChatSerializer", "serialize")]:
        serialize = getattr(_serializer(module, cls), fn)
        for message in history:
            serialize(message)          # must not raise
