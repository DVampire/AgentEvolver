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
from types import SimpleNamespace

import pytest

from agentevolver.agent.native_tools import _SchemaTool
from agentevolver.message.types import (
    AssistantMessage,
    CompactionMessage,
    Function,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from agentevolver.model.llm_hub.response import (
    NativeFeatureUnavailable,
    ResponseLLMHub,
    serialize_input,
    serialize_tools,
)
from agentevolver.model.types import accumulate_stream, buffered_response_to_events


def _tool(name="read_file_tool"):
    return _SchemaTool(
        name=name,
        description="Read a file.",
        function_calling={
            "type": "function",
            "function": {
                "name": name,
                "description": "Read a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
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
    assert specs["response"][0]["native_compaction"] is True
    assert specs["response"][0]["persisted_reasoning"] is True
    assert specs["response"][0]["native_programmatic_tool_calling"] is True
    assert specs["response"][0].get("native_multi_agent", False) is False
    # And it must not still be on the surface that refuses its tools.
    assert not any("5.6-sol" in m["model_name"] for m in specs["chat"])


def test_direct_openai_catalog_declares_the_same_protocol_with_a_tool_safe_fallback():
    from agentevolver.model.config import openai_models

    specs = openai_models(
        max_tokens=8192,
        default_temperature=0.7,
        default_reasoning={"reasoning": {"effort": "low"}},
    )
    sol = next(model for model in specs["response"] if model["model_name"] == "openai/gpt-5.6-sol")

    assert sol["persisted_reasoning"] is True
    assert sol["native_compaction"] is True
    assert sol["native_programmatic_tool_calling"] is True
    assert sol["native_multi_agent"] is True
    assert sol["fallback_model"] == "openai/gpt-4.1"


def test_the_catalog_omits_temperature_for_opus_5():
    """Opus 4.7 and later removed the sampling parameters; sending one is a 400."""
    from agentevolver.model.config import llm_hub_models

    specs = llm_hub_models(max_tokens=8192, default_temperature=0.7, default_timeout=600)
    opus = next(m for m in specs["chat"] if m["model_name"] == "llm_hub/claude-opus-5")

    assert "temperature" not in opus
    assert opus["model_id"] == "claude-opus-5", "the relay refuses the prefixed form"
    assert opus["model_type"] == "anthropic/messages"
    assert opus["native_compaction"] is True

    portable_only = {
        model["model_name"]: model.get("native_compaction", False)
        for model in specs["chat"]
        if model["model_name"] != "llm_hub/claude-opus-5"
    }
    assert portable_only
    assert not any(portable_only.values())


def test_the_client_sends_no_temperature_unless_given_one():
    """A default here would make every Opus 4.7+ model unusable through this provider."""
    from agentevolver.model.llm_hub.chat import ChatLLMHub

    assert ChatLLMHub(model="claude-opus-5").temperature is None


def test_opus_5_native_messages_route_caches_the_tool_catalog():
    from agentevolver.model.anthropic.serializer import AnthropicChatSerializer

    opus = AnthropicChatSerializer.serialize_tools([_tool()])[-1]

    assert opus["cache_control"]["type"] == "ephemeral"


@pytest.mark.asyncio
async def test_llm_hub_builds_opus_5_with_the_native_anthropic_client():
    from agentevolver.model.anthropic.chat import ChatAnthropic
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig

    client = await ModelContextManager()._build_client(
        ModelConfig(
            model_name="llm_hub/claude-opus-5",
            model_id="claude-opus-5",
            model_type="anthropic/messages",
            provider="llm_hub",
            api_key="key",
            api_base="https://relay.invalid/v1",
            reasoning={"thinking": {"type": "adaptive"}},
        )
    )

    assert isinstance(client, ChatAnthropic)
    assert client.model == "claude-opus-5"
    assert str(client.base_url) == "https://relay.invalid/v1"


def test_native_anthropic_stream_keeps_the_signed_thinking_block():
    from agentevolver.model.anthropic.chat import ChatAnthropic

    class Block(SimpleNamespace):
        def model_dump(self, **kwargs):
            return dict(vars(self))

    async def raw():
        yield SimpleNamespace(
            type="content_block_start", index=0, content_block=Block(type="thinking", thinking="")
        )
        yield SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="why"),
        )
        yield SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="signature_delta", signature="sig"),
        )

    acc = asyncio.run(accumulate_stream(ChatAnthropic(model="claude-opus-5")._parse_stream(raw())))
    assert acc["thinking"] == "why"
    assert acc["provider_state"]["anthropic"]["thinking_blocks"] == [
        {
            "type": "thinking",
            "thinking": "why",
            "signature": "sig",
        }
    ]


@pytest.mark.asyncio
async def test_native_anthropic_compaction_returns_a_round_trippable_block(monkeypatch):
    from agentevolver.model.anthropic.chat import ChatAnthropic

    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model_dump=lambda: {
                "stop_reason": "compaction",
                "content": [{"type": "compaction", "content": "summary"}],
                "usage": {
                    "iterations": [
                        {"type": "compaction", "input_tokens": 50_000, "output_tokens": 800},
                    ]
                },
            }
        )

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(ChatAnthropic, "get_client", lambda self: fake)
    client = ChatAnthropic(model="claude-opus-5")

    result = await client.compact_history(
        [
            HumanMessage(content="task" + ("x" * 220_000)),
        ]
    )

    edit = captured["context_management"]["edits"][0]
    assert captured["betas"] == ["compact-2026-01-12"]
    assert edit["type"] == "compact_20260112"
    assert edit["pause_after_compaction"] is True
    assert result["summary"] == "summary"
    assert result["provider_state"]["anthropic"]["compaction_blocks"][0]["type"] == "compaction"
    assert result["usage"]["input_tokens"] == 50_000
    assert result["usage"]["output_tokens"] == 800


def test_anthropic_native_compaction_waits_until_the_beta_minimum():
    from agentevolver.model.anthropic.chat import ChatAnthropic

    assert not ChatAnthropic.compaction_ready([HumanMessage(content="short")])
    assert ChatAnthropic.compaction_ready(
        [
            HumanMessage(content="x" * 220_000),
        ]
    )


@pytest.mark.asyncio
async def test_native_compaction_replay_keeps_context_management_enabled():
    from agentevolver.model.anthropic.chat import ChatAnthropic

    checkpoint = CompactionMessage(
        content="portable",
        provider_state={
            "anthropic": {
                "compaction_blocks": [
                    {
                        "type": "compaction",
                        "content": "canonical summary",
                    }
                ]
            }
        },
    )
    built = await ChatAnthropic(model="claude-opus-5", temperature=None)._build_params(
        [
            HumanMessage(content="task"),
            checkpoint,
            HumanMessage(content="continue"),
        ]
    )

    assert built["use_beta_api"] is True
    assert "compact-2026-01-12" in built["params"]["betas"]
    edit = built["params"]["context_management"]["edits"][0]
    assert edit["type"] == "compact_20260112"
    assert edit["trigger"]["value"] == 900_000
    assert built["params"]["messages"][1]["content"][0]["type"] == "compaction"


def test_direct_anthropic_catalog_exposes_opus_5_native_route():
    from agentevolver.model.config import anthropic_models

    models = anthropic_models(
        max_tokens=16_384,
        default_temperature=0.7,
        default_timeout=600,
        default_plugins=[],
        default_reasoning={},
    )["chat"]
    opus = next(model for model in models if model["model_name"] == "anthropic/claude-opus-5")

    assert opus["model_id"] == "claude-opus-5"
    assert opus["context_window"] == 1_000_000
    assert opus["reasoning"]["thinking"]["type"] == "adaptive"
    assert "temperature" not in opus


def test_llm_hub_stream_keeps_claude_reasoning_extensions():
    from agentevolver.model.llm_hub.chat import ChatLLMHub

    async def raw():
        delta = SimpleNamespace(
            content=None,
            reasoning_content="why",
            tool_calls=[],
            reasoning_details=[{"type": "reasoning.text", "text": "why"}],
            reasoning_signature="sig",
            model_extra={},
        )
        yield SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=delta,
                    finish_reason="stop",
                )
            ],
        )

    acc = asyncio.run(accumulate_stream(ChatLLMHub(model="claude-opus-5")._parse_stream(raw())))
    assert acc["provider_state"]["llm_hub"]["reasoning_signature"] == "sig"
    assert acc["provider_state"]["llm_hub"]["reasoning_details"][0]["text"] == "why"


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
    items = serialize_input(
        [
            HumanMessage(content="read it"),
            AssistantMessage(
                content="looking",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=Function(name="read_file_tool", arguments='{"path":"a.py"}'),
                    )
                ],
            ),
            ToolMessage(content="contents", tool_call_id="call_1"),
        ]
    )

    assert [i.get("type") or i.get("role") for i in items] == [
        "user",
        "assistant",
        "function_call",
        "function_call_output",
    ]
    assert items[2]["call_id"] == items[3]["call_id"] == "call_1"
    assert json.loads(items[2]["arguments"]) == {"path": "a.py"}
    assert items[3]["output"] == "contents"


def test_an_assistant_turn_with_no_text_still_carries_its_calls():
    """A model that calls a tool without narrating must not lose the call."""
    items = serialize_input(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_1", function=Function(name="t", arguments="{}"))],
            )
        ]
    )

    assert [i.get("type") for i in items] == ["function_call"]


def test_system_messages_stay_as_items():
    """`instructions` is a single field, so several system turns would collapse into one."""
    items = serialize_input(
        [SystemMessage(content="a"), SystemMessage(content="b"), HumanMessage(content="c")]
    )

    assert [i["role"] for i in items] == ["system", "system", "user"]


def test_a_native_compaction_item_is_replayed_without_flattening_to_user_text():
    opaque = {"type": "compaction", "encrypted_content": "opaque-1"}
    items = serialize_input(
        [
            CompactionMessage(
                content="readable fallback",
                provider_state={"responses": {"compaction_items": [opaque]}},
            ),
            HumanMessage(content="continue"),
        ]
    )

    assert items == [opaque, {"role": "user", "content": "continue"}]


def test_native_compaction_replaces_user_history_but_keeps_system_instructions():
    canonical = [
        {"type": "message", "role": "user", "content": "task"},
        {"type": "compaction", "encrypted_content": "opaque"},
    ]
    items = serialize_input(
        [
            SystemMessage(content="agent contract"),
            HumanMessage(content="task"),
            CompactionMessage(
                content="portable",
                provider_state={"responses": {"compaction_items": canonical}},
            ),
            HumanMessage(content="continue"),
        ]
    )

    assert items == [
        {"role": "system", "content": "agent contract"},
        *canonical,
        {"role": "user", "content": "continue"},
    ]


@pytest.mark.asyncio
async def test_native_compaction_keeps_the_canonical_output_verbatim():
    async def compact(**kwargs):
        assert kwargs["input"][0] == {"role": "user", "content": "task"}
        return SimpleNamespace(
            model_dump=lambda: {
                "output": [
                    {"type": "message", "role": "user", "content": "task"},
                    {"type": "message", "role": "user", "content": "Error: retry"},
                    {"type": "compaction", "encrypted_content": "opaque-2"},
                ],
                "usage": {"input_tokens": 12},
            }
        )

    client = _client()
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(compact=compact))
    result = await client.compact_history(
        [
            HumanMessage(content="task"),
            HumanMessage(content="Error: retry"),
        ]
    )

    saved = result["provider_state"]["responses"]["compaction_items"]
    assert [item["type"] for item in saved] == ["message", "message", "compaction"]
    assert saved[0]["content"] == "task"
    assert saved[1]["content"] == "Error: retry"
    assert result["usage"] == {"input_tokens": 12}


def test_reasoning_is_accepted_in_either_catalog_shape():
    """One catalog entry can move between the two surfaces without being rewritten."""
    responses_shape = _client(reasoning={"effort": "low"})._build_params(
        [HumanMessage(content="x")]
    )
    chat_shape = _client(reasoning={"reasoning": {"effort": "high"}})._build_params(
        [HumanMessage(content="x")]
    )

    assert responses_shape["reasoning"] == {"effort": "low"}
    assert chat_shape["reasoning"] == {"effort": "high"}


def test_persisted_reasoning_options_are_forwarded_without_chat_vocabulary():
    params = _client(
        reasoning={
            "effort": "low",
            "context": "all_turns",
            "mode": "pro",
        }
    )._build_params([HumanMessage(content="x")])

    assert params["reasoning"] == {
        "effort": "low",
        "context": "all_turns",
        "mode": "pro",
    }


def test_programmatic_tool_calling_only_opts_read_only_tools_in():
    direct = _tool("write_file_tool")
    read = _tool("read_file_tool")
    read.metadata = {"programmatic": True}
    client = _client(native_programmatic_tool_calling=True)

    params = client._build_params(
        [HumanMessage(content="inspect")],
        [direct, read],
        runtime_features={"programmatic_tool_calling": "native"},
    )

    by_type = {tool.get("name", tool["type"]): tool for tool in params["tools"]}
    assert "allowed_callers" not in by_type["write_file_tool"]
    assert by_type["read_file_tool"]["allowed_callers"] == ["direct", "programmatic"]
    assert by_type["programmatic_tool_calling"] == {"type": "programmatic_tool_calling"}


def test_multi_agent_is_an_explicit_beta_request():
    client = _client(
        native_multi_agent=True,
        reasoning={"reasoning": {"effort": "high", "summary": "auto"}},
    )
    params = client._build_params(
        [HumanMessage(content="review")],
        runtime_features={"multi_agent": "native", "max_concurrent_subagents": 2},
        max_tool_calls=10,
        # Stale provider kwargs cannot override the framework's resolved mode.
        multi_agent={"enabled": False},
    )

    assert params["multi_agent"] == {"enabled": True, "max_concurrent_subagents": 2}
    assert params["betas"] == ["responses_multi_agent=v1"]
    assert params["reasoning"] == {"effort": "high"}
    assert "max_tool_calls" not in params


def test_feature_resolution_has_a_named_fallback_for_every_optimization():
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig

    manager = ModelContextManager()
    manager.models["plain"] = ModelConfig(
        model_name="plain",
        model_id="plain",
        model_type="chat/completions",
        provider="test",
    )
    resolved = manager.resolve_runtime_features(
        "plain",
        {
            "programmatic_tool_calling": True,
            "multi_agent": True,
        },
    )

    assert resolved == {
        "persisted_reasoning": "message_replay",
        "compaction": "portable_checkpoint",
        "programmatic_tool_calling": "direct_tools",
        "multi_agent": "local_meta_agent",
        "prompt_cache": "provider_prefix",
    }


def test_responses_client_serializes_structured_output():
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    client = _client()
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "status": "completed",
                "output": [{"type": "message", "content": [{"text": '{"value":1}'}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    response = asyncio.run(client([HumanMessage(content="x")], response_format=Answer))

    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["name"] == "Answer"
    assert response.parsed_model.value == 1


def test_rejected_prompt_cache_is_removed_on_fallback():
    client = _client()
    client._disabled_features.add("prompt_cache")
    params = client._build_params(
        [HumanMessage(content="x")],
        prompt_cache_key="stable",
        prompt_cache_options={"ttl": "30m"},
    )
    assert "prompt_cache_key" not in params
    assert "prompt_cache_options" not in params


def test_manager_records_the_same_automatic_cache_contract_it_sends():
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig

    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="gpt-5.6-sol",
        model_type="responses",
        provider="llm_hub",
        supports_functions=True,
    )
    client = _client()
    wire, snapshot = manager._runtime_call_kwargs(
        "main",
        client,
        {"trace_context": {"agent_name": "meta_agent"}},
        {},
    )

    assert wire["prompt_cache_key"] == snapshot["prompt_cache_key"]
    assert len(wire["prompt_cache_key"]) == 32
    assert snapshot["runtime_features"]["prompt_cache"] == "automatic"

    client._disabled_features.add("prompt_cache")
    wire, snapshot = manager._runtime_call_kwargs(
        "main",
        client,
        {"trace_context": {"agent_name": "meta_agent"}},
        {},
    )
    assert "prompt_cache_key" not in wire and "prompt_cache_key" not in snapshot
    assert snapshot["runtime_features"]["prompt_cache"] == "disabled"


def test_a_reasoning_toggle_with_no_effort_is_not_sent():
    """`{"enabled": true}` is the chat vocabulary; sending it here would be a 400."""
    params = _client(reasoning={"reasoning": {"enabled": True}})._build_params(
        [HumanMessage(content="x")]
    )

    assert "reasoning" not in params


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
_RAW_TOOL_CALL = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": [{"text": "need to read it"}]},
        {
            "type": "function_call",
            "id": "fc_9",
            "call_id": "call_7",
            "name": "read_file_tool",
            "arguments": '{"path": "/tmp/a.py"}',
        },
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
    assert parsed.data["provider_state"]["responses"]["reasoning_items"][0]["type"] == "reasoning"


def test_responses_reasoning_items_are_replayed_before_the_next_call():
    parsed = _client()._parse(_RAW_TOOL_CALL)
    state = parsed.data["provider_state"]
    items = serialize_input(
        [
            AssistantMessage(
                content="",
                provider_state=state,
                tool_calls=[
                    ToolCall(
                        id="call_7",
                        function=Function(name="read_file_tool", arguments='{"path":"/tmp/a.py"}'),
                    )
                ],
            )
        ]
    )

    assert [item["type"] for item in items] == ["reasoning", "function_call"]


def test_complete_response_output_is_replayed_without_reconstruction():
    program = {"type": "program", "call_id": "prog_1", "code": "text('ok')"}
    call = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file_tool",
        "arguments": '{"path":"a"}',
        "caller": {"type": "program", "caller_id": "prog_1"},
    }
    message = AssistantMessage(
        content="ignored reconstruction",
        provider_state={"responses": {"output_items": [program, call]}},
    )
    output = ToolMessage(
        content="contents",
        tool_call_id="call_1",
        caller=call["caller"],
    )

    assert serialize_input([message, output]) == [
        program,
        call,
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "contents",
            "caller": call["caller"],
        },
    ]


@pytest.mark.asyncio
async def test_hosted_continuation_preserves_every_native_output_item():
    requests = []
    replies = [
        {
            "status": "completed",
            "output": [{"type": "program", "call_id": "prog_1", "code": "text('ok')"}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "reason_2", "summary": []},
                {"type": "message", "content": [{"text": "done"}]},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
    ]

    async def create(**params):
        requests.append(params)
        payload = replies.pop(0)
        return SimpleNamespace(model_dump=lambda **_kwargs: payload)

    client = _client()
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))

    response = await client([HumanMessage(content="run the program")])

    assert response.success and response.message == "done"
    state = response.data["provider_state"]["responses"]
    assert [item["type"] for item in state["output_items"]] == [
        "program",
        "reasoning",
        "message",
    ]
    assert state["reasoning_items"][0]["id"] == "reason_2"
    assert requests[1]["input"][-1]["type"] == "program"
    assert response.usage.input_tokens == 22
    assert response.usage.output_tokens == 5


def test_multi_agent_parser_exposes_only_the_root_final_answer():
    parsed = _client()._parse(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "agent": {"agent_name": "/root/a"},
                    "phase": "final_answer",
                    "content": [{"text": "worker"}],
                },
                {
                    "type": "message",
                    "agent": {"agent_name": "/root"},
                    "phase": "final_answer",
                    "content": [{"text": "root"}],
                },
            ],
        }
    )

    assert parsed.message == "root"
    assert len(parsed.data["provider_state"]["responses"]["output_items"]) == 2


@pytest.mark.asyncio
async def test_rejected_native_feature_is_disabled_for_the_route():
    async def create(**kwargs):
        raise RuntimeError("unsupported tool type programmatic_tool_calling")

    client = _client(native_programmatic_tool_calling=True)
    read = _tool()
    read.metadata = {"programmatic": True}
    client._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(create=create),
        beta=SimpleNamespace(),
    )

    with pytest.raises(NativeFeatureUnavailable):
        await client(
            messages=[HumanMessage(content="inspect")],
            tools=[read],
            runtime_features={"programmatic_tool_calling": "native"},
        )

    assert "programmatic_tool_calling" in client._disabled_features
    fallback = client._build_params(
        [HumanMessage(content="inspect")],
        [read],
        runtime_features={"programmatic_tool_calling": "native"},
    )
    assert not any(tool.get("type") == "programmatic_tool_calling" for tool in fallback["tools"])


@pytest.mark.asyncio
async def test_rejection_disables_only_the_named_feature_on_a_shared_request():
    async def create(**kwargs):
        raise RuntimeError("unsupported tool type programmatic_tool_calling")

    client = _client(native_programmatic_tool_calling=True)
    read = _tool()
    read.metadata = {"programmatic": True}
    client._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(create=create),
        beta=SimpleNamespace(),
    )

    with pytest.raises(NativeFeatureUnavailable) as caught:
        await client(
            messages=[HumanMessage(content="inspect")],
            tools=[read],
            runtime_features={"programmatic_tool_calling": "native"},
            prompt_cache_key="stable-prefix",
        )

    assert caught.value.features == ["programmatic_tool_calling"]
    assert client._disabled_features == {"programmatic_tool_calling"}


def test_missing_beta_endpoint_disables_only_multi_agent():
    class MissingEndpoint(RuntimeError):
        status_code = 404

    from agentevolver.model.llm_hub.response import _rejected_features

    assert _rejected_features(
        MissingEndpoint("not found"),
        ["programmatic_tool_calling", "multi_agent", "prompt_cache"],
    ) == ["multi_agent"]


@pytest.mark.asyncio
async def test_transient_failure_does_not_change_the_capability_catalog():
    async def create(**kwargs):
        raise TimeoutError("upstream timed out")

    client = _client(native_programmatic_tool_calling=True)
    read = _tool()
    read.metadata = {"programmatic": True}
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))

    with pytest.raises(TimeoutError):
        await client(
            messages=[HumanMessage(content="inspect")],
            tools=[read],
            runtime_features={"programmatic_tool_calling": "native"},
        )

    assert not client._disabled_features


@pytest.mark.asyncio
async def test_manager_records_native_rejection_then_retries_the_named_fallback(monkeypatch):
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    wire_modes = []
    snapshots = []

    async def create(**params):
        native = any(
            tool.get("type") == "programmatic_tool_calling" for tool in params.get("tools") or []
        )
        wire_modes.append("native" if native else "direct_tools")
        if native:
            raise RuntimeError("programmatic tool calling is unavailable")
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "status": "completed",
                "output": [{"type": "message", "content": [{"text": "OK"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def record(**kwargs):
        snapshots.append(kwargs["call_kwargs"]["runtime_features"])

    client = _client(native_programmatic_tool_calling=True)
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="gpt-5.6-sol",
        model_type="responses",
        provider="llm_hub",
        supports_functions=True,
        native_programmatic_tool_calling=True,
    )
    manager.model_clients["main"] = client
    read = _tool()
    read.metadata = {"programmatic": True}
    monkeypatch.setattr("agentevolver.model.context._record_request_snapshot", record)

    result = await manager(
        name="main",
        input={
            "messages": [HumanMessage(content="inspect")],
            "tools": [read],
            "max_retries": 1,
            "runtime_features": {"programmatic_tool_calling": True},
        },
        ctx=ModelContext(id="session"),
    )

    assert result.success and result.message == "OK"
    assert wire_modes == ["native", "direct_tools"]
    assert [item["programmatic_tool_calling"] for item in snapshots] == [
        "native",
        "direct_tools",
    ]


@pytest.mark.asyncio
async def test_fallback_route_can_itself_downgrade_a_rejected_native_feature(monkeypatch):
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext
    from agentevolver.response.types import Response, ResponseType

    wire_modes = []
    snapshots = []

    async def unavailable_primary(**_kwargs):
        return Response(type=ResponseType.LLM, success=False, message="primary unavailable")

    async def create(**params):
        native = any(
            tool.get("type") == "programmatic_tool_calling" for tool in params.get("tools") or []
        )
        wire_modes.append("native" if native else "direct_tools")
        if native:
            raise RuntimeError("programmatic tool calling is unavailable")
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "status": "completed",
                "output": [{"type": "message", "content": [{"text": "fallback OK"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def record(**kwargs):
        snapshots.append(
            (
                kwargs["routed_model"],
                kwargs["call_kwargs"]["runtime_features"]["programmatic_tool_calling"],
            )
        )

    fallback_client = _client(native_programmatic_tool_calling=True)
    fallback_client._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(create=create),
    )
    manager = ModelContextManager()
    manager.models["primary"] = ModelConfig(
        model_name="primary",
        model_id="primary",
        model_type="responses",
        provider="test",
        fallback_model="fallback",
    )
    manager.model_clients["primary"] = unavailable_primary
    manager.models["fallback"] = ModelConfig(
        model_name="fallback",
        model_id="gpt-5.6-sol",
        model_type="responses",
        provider="llm_hub",
        supports_functions=True,
        native_programmatic_tool_calling=True,
    )
    manager.model_clients["fallback"] = fallback_client
    read = _tool()
    read.metadata = {"programmatic": True}
    monkeypatch.setattr("agentevolver.model.context._record_request_snapshot", record)

    result = await manager(
        name="primary",
        input={
            "messages": [HumanMessage(content="inspect")],
            "tools": [read],
            "max_retries": 1,
            "runtime_features": {"programmatic_tool_calling": True},
        },
        ctx=ModelContext(id="session"),
    )

    assert result.success and result.message == "fallback OK"
    assert wire_modes == ["native", "direct_tools"]
    assert snapshots == [
        ("primary", "direct_tools"),
        ("fallback", "native"),
        ("fallback", "direct_tools"),
    ]


@pytest.mark.asyncio
async def test_native_declaration_does_not_add_an_ordinary_retry(monkeypatch):
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    calls = 0

    async def create(**_params):
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary upstream failure")

    client = _client(native_programmatic_tool_calling=True)
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="gpt-5.6-sol",
        model_type="responses",
        provider="llm_hub",
        supports_functions=True,
        native_programmatic_tool_calling=True,
    )
    manager.model_clients["main"] = client
    read = _tool()
    read.metadata = {"programmatic": True}
    monkeypatch.setattr(
        "agentevolver.model.context._record_request_snapshot",
        lambda **_kwargs: asyncio.sleep(0),
    )

    result = await manager(
        name="main",
        input={
            "messages": [HumanMessage(content="inspect")],
            "tools": [read],
            "max_retries": 1,
            "runtime_features": {"programmatic_tool_calling": True},
        },
        ctx=ModelContext(id="session"),
    )

    assert not result.success
    assert calls == 1


@pytest.mark.asyncio
async def test_background_create_timeout_is_not_retried_or_fallen_back(monkeypatch):
    import httpx

    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    primary_calls = 0
    fallback_calls = 0

    async def primary_create(**_params):
        nonlocal primary_calls
        primary_calls += 1
        raise httpx.ReadTimeout("acknowledgement lost")

    async def fallback_create(**_params):
        nonlocal fallback_calls
        fallback_calls += 1
        raise AssertionError("an uncertain background create must not fall back")

    primary = _client()
    primary._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(create=primary_create),
    )
    fallback = ResponseLLMHub(
        model="fallback",
        api_key="k",
        base_url="http://x/v1",
    )
    fallback._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(create=fallback_create),
    )
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="main",
        model_type="responses",
        provider="llm_hub",
        fallback_model="fallback",
    )
    manager.models["fallback"] = ModelConfig(
        model_name="fallback",
        model_id="fallback",
        model_type="responses",
        provider="llm_hub",
    )
    manager.model_clients.update({"main": primary, "fallback": fallback})
    monkeypatch.setattr(
        "agentevolver.model.context._record_request_snapshot",
        lambda **_kwargs: asyncio.sleep(0, result="snapshot-1"),
    )

    result = await manager(
        name="main",
        input={
            "messages": [HumanMessage(content="long task")],
            "background": True,
            "max_retries": 3,
        },
        ctx=ModelContext(id="session"),
    )

    assert not result.success
    assert result.data["background"]["requires_reconciliation"] is True
    assert primary_calls == 1
    assert fallback_calls == 0


@pytest.mark.asyncio
async def test_prompt_cache_rejection_gets_one_reserved_fallback_attempt(monkeypatch):
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    calls = []

    class UnsupportedCache(RuntimeError):
        status_code = 400

    async def create(**params):
        calls.append(dict(params))
        if params.get("prompt_cache_key"):
            raise UnsupportedCache("unknown parameter prompt_cache_key")
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "status": "completed",
                "output": [{"type": "message", "content": [{"text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    client = _client()
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="gpt-5.6-sol",
        model_type="responses",
        provider="llm_hub",
        supports_functions=True,
    )
    manager.model_clients["main"] = client
    monkeypatch.setattr(
        "agentevolver.model.context._record_request_snapshot",
        lambda **_kwargs: asyncio.sleep(0),
    )

    result = await manager(
        name="main",
        input={"messages": [HumanMessage(content="inspect")], "max_retries": 1},
        ctx=ModelContext(id="session"),
    )

    assert result.success and result.message == "ok"
    assert len(calls) == 2
    assert "prompt_cache_key" in calls[0]
    assert "prompt_cache_key" not in calls[1]


def test_malformed_arguments_are_kept_rather_than_dropped():
    """A model that emits broken JSON should surface it, not silently call with {}."""
    raw = {
        "status": "completed",
        "output": [
            {"type": "function_call", "call_id": "c", "name": "t", "arguments": "{not json"}
        ],
    }

    assert _client()._parse(raw).data["functions"][0]["args"] == {"__raw__": "{not json"}


def test_a_text_answer_parses_to_text():
    raw = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
    }
    parsed = _client()._parse(raw)

    assert parsed.data["text"] == "OK"
    assert parsed.data["functions"] == []
    assert parsed.data["finish_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_background_create_retrieve_and_cancel_are_explicit_lifecycle_calls():
    calls = []

    async def create(**params):
        calls.append(("create", params))
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "id": "resp_1",
                "status": "queued",
                "output": [],
            }
        )

    async def retrieve(response_id):
        calls.append(("retrieve", response_id))
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "id": response_id,
                "status": "completed",
                "output": [{"type": "message", "content": [{"text": "done"}]}],
            }
        )

    async def cancel(response_id):
        calls.append(("cancel", response_id))
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "id": response_id,
                "status": "cancelled",
                "output": [],
            }
        )

    client = _client()
    client._client = lambda: SimpleNamespace(
        responses=SimpleNamespace(
            create=create,
            retrieve=retrieve,
            cancel=cancel,
        )
    )

    queued = await client.create_background([HumanMessage(content="long task")])
    completed = await client.retrieve_background("resp_1")
    cancelled = await client.cancel_background("resp_1")

    assert queued.success and queued.data["background"] == {
        "response_id": "resp_1",
        "status": "queued",
    }
    assert calls[0][1]["background"] is True and calls[0][1]["store"] is True
    assert completed.message == "done"
    assert cancelled.success and cancelled.data["background"]["status"] == "cancelled"
    assert [item[0] for item in calls] == ["create", "retrieve", "cancel"]


@pytest.mark.asyncio
async def test_native_responses_stream_emits_incremental_canonical_events():
    from agentevolver.model.types import (
        ProviderState,
        StreamDone,
        TextDelta,
        ThinkingDelta,
        ToolCallArgsDelta,
        ToolCallStart,
    )

    payloads = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "r1"},
        },
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "checking"},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "call_id": "c1", "name": "read_file_tool"},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '"a.py"}'},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": "r1", "summary": [{"text": "checking"}]},
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "read_file_tool",
                        "arguments": '{"path":"a.py"}',
                    },
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        },
    ]

    class Events:
        def __aiter__(self):
            async def values():
                for payload in payloads:
                    yield payload

            return values()

    async def create(**params):
        assert params["stream"] is True
        return Events()

    client = _client()
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    events = [
        event
        async for event in client.stream(
            [HumanMessage(content="inspect")],
            tools=[_tool()],
        )
    ]

    assert any(isinstance(event, ThinkingDelta) and event.text == "checking" for event in events)
    assert any(isinstance(event, ToolCallStart) and event.id == "c1" for event in events)
    assert (
        "".join(event.partial_json for event in events if isinstance(event, ToolCallArgsDelta))
        == '{"path":"a.py"}'
    )
    state = next(event for event in events if isinstance(event, ProviderState))
    assert state.data["responses"]["output_items"][1]["call_id"] == "c1"
    done = next(event for event in events if isinstance(event, StreamDone))
    assert done.stop_reason == "tool_use" and done.usage["input_tokens"] == 4
    assert not any(isinstance(event, TextDelta) for event in events)


def test_explicit_previous_response_id_is_sent_and_snapshotted():
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig

    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="model",
        model_type="responses",
        provider="llm_hub",
    )
    client = _client()
    wire, snapshot = manager._runtime_call_kwargs(
        "main",
        client,
        {"previous_response_id": "resp_previous"},
        {},
    )
    assert wire["previous_response_id"] == "resp_previous"
    assert snapshot["previous_response_id"] == "resp_previous"


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
    assert acc["provider_state"] == parsed.data["provider_state"]


def test_response_format_is_refused_loudly_rather_than_reinterpreted():
    """Quietly turning a schema into a text hint yields output that looks checked and is not."""

    async def run():
        client = _client()
        client._client = lambda: (_ for _ in ()).throw(RuntimeError("must not reach the network"))
        with pytest.raises(RuntimeError):
            await client(messages=[HumanMessage(content="x")], response_format=dict)

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# A derived history, on the Responses surface
# --------------------------------------------------------------------------- #
def test_a_derived_history_becomes_valid_response_items():
    """What `derive_context` produces, through the surface `gpt-5.6-sol` is routed to.

    That model refuses function tools on chat/completions, so it is the only path this
    agent loop can use for it — and the projection's `[user, assistant(+tool_calls),
    tool]` had never been sent through it. Chat attaches calls to the assistant message;
    Responses makes the call and its result separate items. `tool_call_id` is the hinge,
    and it is the same value on both sides.
    """
    from agentevolver.message.types import (
        AssistantMessage,
        Function,
        HumanMessage,
        ToolCall,
        ToolMessage,
    )
    from agentevolver.model.llm_hub.response import serialize_input

    history = [
        HumanMessage(content="write hello.py"),
        AssistantMessage(
            content="I'll write it.",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    function=Function(name="write_file_tool", arguments='{"path": "hello.py"}'),
                )
            ],
        ),
        ToolMessage(content="Created hello.py", tool_call_id="call_1", name="write_file_tool"),
    ]
    items = serialize_input(history)

    assert [i.get("type") or i.get("role") for i in items] == [
        "user",
        "assistant",
        "function_call",
        "function_call_output",
    ]

    call = next(i for i in items if i.get("type") == "function_call")
    result = next(i for i in items if i.get("type") == "function_call_output")
    assert call["call_id"] == result["call_id"] == "call_1", (
        "the result must echo the call_id, which is what pairs them"
    )
    assert result["output"] == "Created hello.py"


def test_an_assistant_turn_with_no_text_contributes_only_its_calls():
    """A step that called a tool without narrating is the common case, not an edge one.

    An empty `content` item is rejected by the surface, so it must be dropped rather
    than sent as "".
    """
    from agentevolver.message.types import AssistantMessage, Function, ToolCall
    from agentevolver.model.llm_hub.response import serialize_input

    items = serialize_input(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_1", function=Function(name="t", arguments="{}"))],
            )
        ]
    )

    assert [i.get("type") for i in items] == ["function_call"]


def test_the_responses_surface_reports_its_cache_counts_under_a_different_name():
    """`input_tokens_details`, not `prompt_tokens_details`.

    A name `from_raw` does not know reads as "nothing was cached" rather than "not
    reported", and the two are indistinguishable downstream — so a whole surface gets
    written off as uncacheable without anyone seeing a zero appear.
    """
    from agentevolver.model.types import TokenUsage

    usage = TokenUsage.from_raw(
        {
            "input_tokens": 57,
            "output_tokens": 29,
            "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 17},
        }
    )
    assert usage.cache_read_tokens == 40
    assert usage.cache_write_tokens == 17


def test_canonical_usage_can_be_repriced_without_losing_cache_counts():
    from agentevolver.model.types import TokenUsage, price_usage_dict

    canonical = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        cache_write_tokens=7,
        cache_read_tokens=11,
    ).model_dump()
    priced = price_usage_dict(
        canonical,
        {
            "input": 1.0,
            "output": 1.0,
            "cache_write": 1.0,
            "cache_read": 1.0,
        },
    )

    assert priced["cache_write_tokens"] == 7
    assert priced["cache_read_tokens"] == 11
    assert priced["cost"] == 33.0
