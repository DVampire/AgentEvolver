"""Every provider must serialize every message the agent can produce.

Two halves of one question, so they live together: whether a serializer *handles* each
`Message` subclass at all, and whether the shape it produces is the one that provider
accepts. The first is discovered — every subclass, every provider package — so a type or
a provider added later is covered by existing rather than by being remembered. The second
is written out per provider, because no two agree: chat/completions has a `tool` role,
Responses has `function_call_output`, Anthropic has a `tool_result` block on a user turn,
and Gemini pairs by declared function *name* and never sees the call id.

`derive_context` produces `ToolMessage`, and not one of the six knew it. The path that
produces the type is off by default, so the gap sat there until the switch was turned on
— and then failed on every step after the first while the run reported success.
"""

from pathlib import Path
import importlib
import inspect
import pkgutil
import pytest

import agentevolver.message.types as message_types
import agentevolver.model as model_package
from agentevolver.message.types import (AssistantMessage, CompactionMessage, Function,
                                        HumanMessage, Message, SystemMessage, ToolCall,
                                        ToolMessage)


# ---------------------------------------------------------------------------
# from test_serializers_cover_every_message_type.py
# ---------------------------------------------------------------------------

# One instance per subclass. Constructed here rather than generated, because a message's
# required fields carry meaning a factory would have to invent — `tool_call_id` is the
# hinge every provider pairs on, and a blank one would pass a gate the wire rejects.
SAMPLES = {
    SystemMessage: SystemMessage(content="rules"),
    HumanMessage: HumanMessage(content="do the thing"),
    CompactionMessage: CompactionMessage(content="checkpoint"),
    AssistantMessage: AssistantMessage(content="on it", tool_calls=[ToolCall(
        id="call_1", function=Function(name="write_file_tool", arguments='{"path": "a"}'))]),
    ToolMessage: ToolMessage(content="wrote a", tool_call_id="call_1",
                             name="write_file_tool"),
}


def _message_subclasses():
    return {v for v in vars(message_types).values()
            if inspect.isclass(v) and issubclass(v, Message) and v is not Message}


def _serializers():
    """Every `*Serializer` class in every provider package, with its entry point.

    Providers name the entry point differently (`serialize` vs `serialize_message`), so
    the gate finds it rather than assuming — an assumption here would silently skip a
    provider and report green.
    """
    found = []
    root = Path(model_package.__file__).parent
    for module_info in pkgutil.iter_modules([str(root)]):
        if not module_info.ispkg:
            continue
        try:
            module = importlib.import_module(
                f"agentevolver.model.{module_info.name}.serializer")
        except ModuleNotFoundError:
            continue
        for name, obj in vars(module).items():
            if not (inspect.isclass(obj) and name.endswith("Serializer")):
                continue
            if obj.__module__ != module.__name__:
                continue          # re-exported from elsewhere; covered at its own module
            entry = next((f for f in ("serialize_message", "serialize")
                          if callable(getattr(obj, f, None))), None)
            if entry:
                found.append((f"{module_info.name}.{name}", obj, entry))
    return found


SERIALIZERS = _serializers()


def test_something_was_actually_found():
    """A test that discovers nothing passes vacuously and checks nothing."""
    assert len(SERIALIZERS) >= 5, f"expected every provider; found {SERIALIZERS}"
    assert _message_subclasses(), "no Message subclasses found"


def test_every_subclass_has_a_sample():
    """Adding a message type must fail here, not at runtime on one provider.

    This is the half a per-serializer test cannot cover: the six serializers were each
    self-consistent and all wrong together.
    """
    missing = _message_subclasses() - set(SAMPLES)
    assert not missing, (
        f"no sample for {sorted(c.__name__ for c in missing)} — add one to SAMPLES so "
        f"every serializer is checked against it")


@pytest.mark.parametrize("label,serializer,entry", SERIALIZERS,
                         ids=[label for label, _, _ in SERIALIZERS])
def test_a_serializer_handles_every_message_type(label, serializer, entry):
    serialize = getattr(serializer, entry)
    failures = []
    for cls, sample in SAMPLES.items():
        try:
            result = serialize(sample)
        except Exception as error:                                  # noqa: BLE001
            failures.append(f"{cls.__name__}: {type(error).__name__}: {error}")
            continue
        if not result:
            failures.append(f"{cls.__name__}: serialized to {result!r}")
    assert not failures, f"{label} cannot serialize:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("label,serializer,entry", SERIALIZERS,
                         ids=[label for label, _, _ in SERIALIZERS])
def test_a_tool_result_keeps_whatever_pairs_it(label, serializer, entry):
    """Serializing without error is not enough; the pairing has to survive.

    A result the provider cannot match to its call is rejected outright — "each
    tool_result block must have a corresponding tool_use block" — so a serializer can
    pass the test above and still produce an unusable history.

    What does the pairing is provider-specific, and stating it as "the id must appear"
    is wrong: Gemini matches a `function_response` by declared function *name* and never
    sees the id. This caught that on its first run, against its own first draft. So
    the invariant is the weaker true one — one of the two identifiers survives — and each
    provider's exact shape is pinned in `test_tool_message_roundtrip.py`.
    """
    sample = SAMPLES[ToolMessage]
    result = repr(getattr(serializer, entry)(sample))
    assert sample.tool_call_id in result or (sample.name and sample.name in result), (
        f"{label} kept neither the call id nor the tool name; nothing pairs the result "
        f"to the call that produced it")

# ---------------------------------------------------------------------------
# from test_tool_message_roundtrip.py
# ---------------------------------------------------------------------------
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


def test_anthropic_coalesces_parallel_results_and_honours_rolling_cache():
    serializer = _serializer("anthropic", "AnthropicChatSerializer")
    first = ToolMessage(content="one", tool_call_id="c1", name="t")
    second = ToolMessage(content="two", tool_call_id="c2", name="t", cache=True)
    _, messages = serializer.serialize_messages([
        AssistantMessage(content="", tool_calls=[
            ToolCall(id="c1", function=Function(name="t", arguments="{}")),
            ToolCall(id="c2", function=Function(name="t", arguments="{}")),
        ]),
        first,
        second,
    ])
    assert [message["role"] for message in messages] == ["assistant", "user"]
    assert len(messages[1]["content"]) == 2
    assert messages[1]["content"][-1]["cache_control"]["type"] == "ephemeral"


def test_anthropic_replays_signed_thinking_before_tool_use():
    serializer = _serializer("anthropic", "AnthropicChatSerializer")
    thinking = {"type": "thinking", "thinking": "private", "signature": "signed"}
    out = serializer.serialize(AssistantMessage(
        content="",
        provider_state={"anthropic": {"thinking_blocks": [thinking]}},
        tool_calls=[ToolCall(id="c1", function=Function(name="t", arguments="{}"))],
    ))

    assert out["content"][0] == thinking
    assert out["content"][1]["type"] == "tool_use"


def test_llm_hub_replays_claude_reasoning_extensions():
    serializer = _serializer("llm_hub", "LLMHubChatSerializer")
    out = serializer.serialize_message(AssistantMessage(
        content="",
        provider_state={"llm_hub": {
            "reasoning_content": "private",
            "reasoning_signature": "signed",
        }},
    ))

    assert out["reasoning_content"] == "private"
    assert out["reasoning_signature"] == "signed"


def test_responses_api_replays_assistant_function_calls_before_outputs():
    serializer = _serializer("openai", "OpenAIResponseSerializer")
    call = ToolCall(id="call_1", function=Function(name="write_file_tool", arguments='{"path":"a"}'))
    out = serializer.serialize_messages([
        AssistantMessage(content="", tool_calls=[call]), RESULT,
    ])
    assert out[0] == {
        "type": "function_call", "call_id": "call_1",
        "name": "write_file_tool", "arguments": '{"path":"a"}',
    }
    assert out[1]["type"] == "function_call_output"


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
