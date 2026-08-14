"""Reasoning models that refuse tools on chat/completions are routed to responses.

`gpt-5.6-sol` answers a tools request on `/v1/chat/completions` with "use /v1/responses
or set reasoning_effort to 'none'". Every AgentEvolver agent loop is tool calling, so
turning reasoning off would give up the reason for picking that model — the model is
routed to the other surface instead.

The existing `openai/response.py` could not be reused: it drops tools
(`# params["tools"] = tools  # Uncomment if supported`) and has no streaming. These
tests pin the parts of the new client that the agent loop actually depends on.
"""

import asyncio
import json

import pytest

from agentevolver.agent.native_tools import _SchemaTool
from agentevolver.message.types import (
    AssistantMessage,
    Function,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from agentevolver.model.llm_hub.response import (
    ResponseLLMHub,
    serialize_input,
    serialize_tools,
)
from agentevolver.model.types import accumulate_stream, buffered_response_to_events


def _tool(name="read_file_tool"):
    return _SchemaTool(
        name=name, description="Read a file.",
        function_calling={"type": "function", "function": {
            "name": name, "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                           "required": ["path"]}}},
    )


def _client(**kwargs):
    return ResponseLLMHub(model="gpt-5.6-sol", api_key="k", base_url="http://x/v1", **kwargs)


# --------------------------------------------------------------------------- #
# Catalog and dispatch
# --------------------------------------------------------------------------- #
def test_the_catalog_routes_the_model_to_responses():
    from agentevolver.model.config import llm_hub_models

    specs = llm_hub_models(max_tokens=8192, default_temperature=0.7, default_timeout=600)

    assert [m["model_name"] for m in specs["response"]] == ["llm_hub/gpt-5.6-sol"]
    assert specs["response"][0]["model_type"] == "responses"
    # And it must not still be on the surface that refuses its tools.
    assert not any("5.6-sol" in m["model_name"] for m in specs["chat"])


def test_the_catalog_omits_temperature_for_opus_5():
    """Opus 4.7 and later removed the sampling parameters; sending one is a 400."""
    from agentevolver.model.config import llm_hub_models

    specs = llm_hub_models(max_tokens=8192, default_temperature=0.7, default_timeout=600)
    opus = next(m for m in specs["chat"] if m["model_name"] == "llm_hub/claude-opus-5")

    assert "temperature" not in opus
    assert opus["model_id"] == "claude-opus-5", "the relay refuses the prefixed form"


def test_the_client_sends_no_temperature_unless_given_one():
    """A default here would make every Opus 4.7+ model unusable through this provider."""
    from agentevolver.model.llm_hub.chat import ChatLLMHub

    assert ChatLLMHub(model="claude-opus-5").temperature is None


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def test_tool_schemas_are_flattened():
    """Responses takes the function fields at the top level; chat nests them."""
    serialized = serialize_tools([_tool()])[0]

    assert serialized["type"] == "function"
    assert serialized["name"] == "read_file_tool"
    assert "function" not in serialized, "still in the chat shape"
    assert serialized["parameters"]["required"] == ["path"]


def test_a_tool_turn_becomes_separate_call_and_output_items():
    """The two APIs disagree about what a turn is; `tool_call_id` is the hinge."""
    items = serialize_input([
        HumanMessage(content="read it"),
        AssistantMessage(content="looking", tool_calls=[ToolCall(
            id="call_1", function=Function(name="read_file_tool", arguments='{"path":"a.py"}'))]),
        ToolMessage(content="contents", tool_call_id="call_1"),
    ])

    assert [i.get("type") or i.get("role") for i in items] == [
        "user", "assistant", "function_call", "function_call_output",
    ]
    assert items[2]["call_id"] == items[3]["call_id"] == "call_1"
    assert json.loads(items[2]["arguments"]) == {"path": "a.py"}
    assert items[3]["output"] == "contents"


def test_an_assistant_turn_with_no_text_still_carries_its_calls():
    """A model that calls a tool without narrating must not lose the call."""
    items = serialize_input([AssistantMessage(content="", tool_calls=[ToolCall(
        id="call_1", function=Function(name="t", arguments="{}"))])])

    assert [i.get("type") for i in items] == ["function_call"]


def test_system_messages_stay_as_items():
    """`instructions` is a single field, so several system turns would collapse into one."""
    items = serialize_input([SystemMessage(content="a"), SystemMessage(content="b"),
                             HumanMessage(content="c")])

    assert [i["role"] for i in items] == ["system", "system", "user"]


def test_reasoning_is_accepted_in_either_catalog_shape():
    """One catalog entry can move between the two surfaces without being rewritten."""
    responses_shape = _client(reasoning={"effort": "low"})._build_params([HumanMessage(content="x")])
    chat_shape = _client(reasoning={"reasoning": {"effort": "high"}})._build_params(
        [HumanMessage(content="x")])

    assert responses_shape["reasoning"] == {"effort": "low"}
    assert chat_shape["reasoning"] == {"effort": "high"}


def test_a_reasoning_toggle_with_no_effort_is_not_sent():
    """`{"enabled": true}` is the chat vocabulary; sending it here would be a 400."""
    params = _client(reasoning={"reasoning": {"enabled": True}})._build_params(
        [HumanMessage(content="x")])

    assert "reasoning" not in params


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
_RAW_TOOL_CALL = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": [{"text": "need to read it"}]},
        {"type": "function_call", "id": "fc_9", "call_id": "call_7",
         "name": "read_file_tool", "arguments": '{"path": "/tmp/a.py"}'},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
}


def test_a_function_call_is_parsed_with_its_call_id():
    """`call_id` is what a later output item must echo; `id` names the item itself."""
    parsed = _client()._parse(_RAW_TOOL_CALL)
    call = parsed.data["functions"][0]

    assert call["id"] == "call_7", "must be call_id, not the item id"
    assert call["name"] == "read_file_tool"
    assert call["args"] == {"path": "/tmp/a.py"}
    assert parsed.data["reasoning"] == "need to read it"


def test_malformed_arguments_are_kept_rather_than_dropped():
    """A model that emits broken JSON should surface it, not silently call with {}."""
    raw = {"status": "completed", "output": [
        {"type": "function_call", "call_id": "c", "name": "t", "arguments": "{not json"}]}

    assert _client()._parse(raw).data["functions"][0]["args"] == {"__raw__": "{not json"}


def test_a_text_answer_parses_to_text():
    raw = {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
    parsed = _client()._parse(raw)

    assert parsed.data["text"] == "OK"
    assert parsed.data["functions"] == []
    assert parsed.data["finish_reason"] == "end_turn"


# --------------------------------------------------------------------------- #
# The buffered result must become the canonical stream the agent loop reads
# --------------------------------------------------------------------------- #
def test_the_result_replays_as_canonical_stream_events():
    """`stream()` buffers this client, so `data` must use the keys that adapter reads."""
    parsed = _client()._parse(_RAW_TOOL_CALL)
    acc = asyncio.run(accumulate_stream(buffered_response_to_events(parsed)))

    assert acc["stop_reason"] == "tool_use"
    assert len(acc["tool_calls"]) == 1
    call = acc["tool_calls"][0]
    assert (call.id, call.name, call.input) == ("call_7", "read_file_tool", {"path": "/tmp/a.py"})
    assert acc["thinking"] == "need to read it"


def test_response_format_is_refused_loudly_rather_than_reinterpreted():
    """Quietly turning a schema into a text hint yields output that looks checked and is not."""
    async def run():
        client = _client()
        client._client = lambda: (_ for _ in ()).throw(RuntimeError("must not reach the network"))
        with pytest.raises(RuntimeError):
            await client(messages=[HumanMessage(content="x")], response_format=dict)

    asyncio.run(run())
