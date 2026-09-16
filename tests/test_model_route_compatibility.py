"""Model optimizations may degrade, but inputs and tool identity may not disappear."""

from types import SimpleNamespace

import pytest

from agentevolver.message.types import (
    AssistantMessage, CompactionMessage, ContentPartImage, ContentPartText,
    Function, HumanMessage, ImageURL, SystemMessage, ToolCall, ToolMessage,
)
from agentevolver.model.capabilities import project_messages
from agentevolver.model.config import llm_hub_models
from agentevolver.model.llm_hub.response import ResponseLLMHub, serialize_input


def client(**kwargs):
    return ResponseLLMHub(model="gpt-6-astra", api_key="test", **kwargs)


def test_images_survive_with_or_without_cache():
    message = HumanMessage(content=[
        ContentPartText(text="Look"),
        ContentPartImage(image_url=ImageURL(url="data:image/png;base64,test", detail="original")),
    ], cache=True)
    for enabled in (False, True):
        wire = serialize_input([message], cache=enabled)[0]["content"]
        assert wire[1]["type"] == "input_image"
        assert wire[1]["image_url"] == "data:image/png;base64,test"
        assert wire[1]["detail"] == "original"
        assert ("prompt_cache_breakpoint" in wire[1]) is enabled
    assert not hasattr(message.content[1], "prompt_cache_breakpoint")


def test_configuration_update_keeps_baseline_and_has_portable_fallback():
    c = client(reasoning={"effort": "high"}, native_configuration_updates=True)
    messages = [SystemMessage(content="fixed"), HumanMessage(content="task")]
    native = c._build_params(messages, reasoning={"effort": "low"})
    assert native["reasoning"] == {"effort": "high"}
    assert native["input"][1] == {"type": "configuration_update", "reasoning": {"effort": "low"}}
    assert native["input"][2]["role"] == "user"
    stateful = c._build_params(messages, reasoning={"effort": "low"}, previous_response_id="resp")
    assert stateful["reasoning"] == {"effort": "low"}
    assert len(stateful["input"]) == 2
    c._disabled_features.add("configuration_updates")
    portable = c._build_params(messages, reasoning={"effort": "low"})
    assert portable["reasoning"] == {"effort": "low"} and len(portable["input"]) == 2


@pytest.mark.asyncio
async def test_wire_audit_observes_images_without_leaking_payload(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    observed = []

    async def create(**params):
        observed.append(params)
        return {"status": "completed", "output": [{"type": "message", "content": [{"text": "ok"}]}]}

    emit = AsyncMock(return_value=True)
    monkeypatch.setattr("agentevolver.trace.server.trace_manager.emit", emit)
    c = client()
    c._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    result = await c(messages=[HumanMessage(content=[
        ContentPartText(text="private text"),
        ContentPartImage(image_url=ImageURL(url="data:image/png;base64,PRIVATE")),
    ])], _request_trace={"session_id": "s", "snapshot_id": "sha256:canonical", "agent_name": "browser"})
    assert result.success
    assert "_request_trace" not in observed[0]
    record = emit.call_args.args[0]
    assert record.metadata["input_types"]["input_image"] == 1
    assert record.metadata["request_snapshot_id"] == "sha256:canonical"
    assert "PRIVATE" not in json.dumps(record.model_dump(), default=str)
    assert "private text" not in json.dumps(record.model_dump(), default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [[], [{"type": "text", "text": "partial summary"}]])
async def test_claude_incomplete_summary_is_not_published(content):
    from agentevolver.model.anthropic.chat import ChatAnthropic
    c = ChatAnthropic(model="claude-opus-5", api_key="test")
    result = await c._format_response({"content": content, "stop_reason": "max_tokens", "usage": {"input_tokens": 10, "output_tokens": 20}})
    assert not result.success and not result.data["retryable"]
    assert not result.data["functions"]


@pytest.mark.asyncio
async def test_claude_wire_audit_is_not_sent_to_sdk(monkeypatch):
    from unittest.mock import AsyncMock
    from agentevolver.model.anthropic.chat import ChatAnthropic
    c = ChatAnthropic(model="claude-opus-5", api_key="test")
    create = AsyncMock(return_value={})
    c.get_client = lambda: SimpleNamespace(messages=SimpleNamespace(create=create))
    emit = AsyncMock(return_value=True)
    monkeypatch.setattr("agentevolver.trace.server.trace_manager.emit", emit)
    built = {"params": {"model": c.model, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "data": "PRIVATE", "media_type": "image/png"}},
    ]}], "_request_trace": {"session_id": "s", "snapshot_id": "canonical"}}}
    await c._call_model(built)
    await c._open_stream(built)
    assert all("_request_trace" not in call.kwargs for call in create.call_args_list)
    assert emit.await_count == 2
    assert emit.call_args.args[0].metadata["surface"] == "anthropic/messages"
    assert emit.call_args.args[0].metadata["input_types"]["image"] == 1
    assert emit.call_args.args[0].metadata["stream"]


@pytest.mark.parametrize("status,details,finish", [
    ("incomplete", {"reason": "max_output_tokens"}, "max_output_tokens"),
    ("incomplete", {"reason": "content_filter"}, "content_filter"),
    ("cancelled", {}, "cancelled"),
])
def test_incomplete_output_cannot_execute_tools_or_become_a_checkpoint(status, details, finish):
    result = client()._parse({"status": status, "incomplete_details": details, "output": [
        {"type": "message", "content": [{"text": "partial checkpoint"}]},
        {"type": "function_call", "call_id": "c", "name": "write", "arguments": '{"path":'},
    ]})
    assert not result.success and not result.data["retryable"]
    assert result.data["functions"] == []
    assert result.data["finish_reason"] == finish
    assert result.message != "partial checkpoint"
    assert result.data["text"] == "partial checkpoint"  # retained for diagnosis only


@pytest.mark.asyncio
async def test_output_limit_is_not_retried_by_model_manager(monkeypatch):
    from unittest.mock import AsyncMock
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    create = AsyncMock(return_value={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []})
    c = client()
    c._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["test"] = ModelConfig(model_name="test", model_id="gpt-6-astra", model_type="responses", provider="llm_hub")
    manager.model_clients["test"] = c
    monkeypatch.setattr("agentevolver.model.context._record_request_snapshot", AsyncMock(return_value=None))
    result = await manager(name="test", input={"messages": [HumanMessage(content="summarize")], "max_retries": 3}, ctx=ModelContext(id="probe"))
    assert not result.success
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_configuration_rejection_retries_once_with_ordinary_effort(monkeypatch):
    from unittest.mock import AsyncMock
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    class Unsupported(RuntimeError):
        status_code = 400

    calls = []

    async def create(**params):
        calls.append(params)
        if any(i.get("type") == "configuration_update" for i in params["input"]):
            raise Unsupported("unsupported configuration_update")
        return {"status": "completed", "output": [{"type": "message", "content": [{"text": "ok"}]}]}

    c = client(reasoning={"effort": "high"}, native_configuration_updates=True)
    c._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["test"] = ModelConfig(model_name="test", model_id="gpt-6-astra", model_type="responses", provider="llm_hub", reasoning={"effort": "high"}, native_configuration_updates=True)
    manager.model_clients["test"] = c
    monkeypatch.setattr("agentevolver.model.context._record_request_snapshot", AsyncMock(return_value=None))
    result = await manager(name="test", input={"messages": [HumanMessage(content="hi")], "reasoning_effort": "low", "max_retries": 1}, ctx=ModelContext(id="probe"))
    assert result.success and len(calls) == 2
    assert calls[0]["reasoning"]["effort"] == "high"
    assert calls[1]["reasoning"]["effort"] == "low"
    assert "configuration_updates" in c._disabled_features


def test_live_duplicate_text_is_not_accidentally_cached():
    wire = serialize_input([
        HumanMessage(content="same", cache=True), HumanMessage(content="same"),
    ], cache=True)
    assert wire[0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert wire[1]["content"] == "same"


def test_rolling_cache_ends_on_tool_results_without_changing_call_ids():
    messages = [HumanMessage(content="goal", cache=True)]
    messages.extend(ToolMessage(content=str(i), tool_call_id=f"call-{i}") for i in range(4))
    wire = serialize_input(messages, cache=True)
    assert wire[1]["output"] == "0" and wire[2]["output"] == "1"
    assert [item["call_id"] for item in wire[3:]] == ["call-2", "call-3"]
    assert wire[-1]["output"][0] == {
        "type": "input_text", "text": "3", "prompt_cache_breakpoint": {"mode": "explicit"},
    }
    assert messages[-1].content == "3"


def test_cache_rejection_removes_markers_and_options_not_images():
    c = client(explicit_prompt_cache=True)
    messages = [HumanMessage(content="fixed", cache=True)]
    assert "prompt_cache_options" in c._build_params(messages)
    c._disabled_features.add("prompt_cache")
    wire = c._build_params(messages, prompt_cache_key="key", prompt_cache_options={"mode": "explicit"})
    assert "prompt_cache_options" not in wire
    assert "prompt_cache_key" not in wire
    assert wire["input"] == [{"role": "user", "content": "fixed"}]


def test_unsupported_route_keeps_automatic_cache_without_new_parameters():
    wire = client()._build_params([HumanMessage(content="fixed", cache=True)])
    assert "prompt_cache_options" not in wire
    assert wire["input"][0]["content"] == "fixed"


def test_reasoning_override_preserves_context_and_respects_route_limits():
    c = client(reasoning={"effort": "high", "context": "all_turns"},
               reasoning_efforts=["low", "medium", "high"], supports_sampling=False)
    wire = c._build_params([], reasoning={"effort": "none"}, temperature=0.7, top_p=0.8)
    assert wire["reasoning"] == {"effort": "low", "context": "all_turns"}
    assert "temperature" not in wire and "top_p" not in wire
    c._disabled_features.add("persisted_reasoning")
    assert c._build_params([])["reasoning"] == {"effort": "high"}
    assert c.reasoning["context"] == "all_turns"


def test_cross_model_fallback_uses_text_checkpoint_and_canonical_tool_calls():
    opaque = {"responses": {"model": "gpt-6-astra", "output_items": [
        {"type": "reasoning", "encrypted_content": "secret-state"},
    ]}}
    source = [
        CompactionMessage(content="Keep the user's goal", provider_state={"responses": {
            "model": "gpt-6-astra", "compaction_items": [{"type": "compaction"}],
        }}),
        AssistantMessage(content="", provider_state=opaque, tool_calls=[ToolCall(
            id="call-1", function=Function(name="read", arguments="{}"), caller={"type": "program"},
        )]),
        ToolMessage(content="file content", tool_call_id="call-1", caller={"type": "program"}),
    ]
    same = project_messages(source, SimpleNamespace(model_type="responses", model_id="gpt-6-astra"))
    assert same[1] is source[1]
    for surface in ("responses", "anthropic/messages", "chat/completions"):
        projected = project_messages(source, SimpleNamespace(model_type=surface, model_id="another-model"))
        assert projected[0].text == "Keep the user's goal"
        assert projected[0].provider_state == projected[1].provider_state == {}
        assert projected[1].tool_calls[0].id == projected[2].tool_call_id == "call-1"
        assert projected[1].tool_calls[0].caller is projected[2].caller is None
    assert source[1].provider_state == opaque
    assert source[2].caller is not None


def test_opaque_only_checkpoint_refuses_lossy_model_switch():
    message = CompactionMessage(content="", provider_state={"responses": {
        "model": "gpt-6-astra", "compaction_items": [{"type": "compaction"}],
    }})
    with pytest.raises(ValueError, match="portable summary"):
        project_messages([message], SimpleNamespace(model_type="responses", model_id="other"))


def test_claude_state_is_preserved_only_on_its_originating_model():
    message = AssistantMessage(content="visible answer", provider_state={"anthropic": {
        "model": "claude-opus-5", "thinking_blocks": [{"type": "thinking", "signature": "signed"}],
    }})
    same = SimpleNamespace(model_type="anthropic/messages", model_id="claude-opus-5")
    assert project_messages([message], same)[0] is message
    for surface in ("responses", "anthropic/messages", "chat/completions"):
        target = SimpleNamespace(model_type=surface, model_id="other")
        projected = project_messages([message], target)[0]
        assert projected.text == message.text
        assert not projected.provider_state
    assert message.provider_state["anthropic"]["thinking_blocks"]


@pytest.mark.asyncio
async def test_gpt6_model_registration_and_client_keep_capability_policy(monkeypatch):
    from agentevolver.model.context import ModelContextManager

    manager = ModelContextManager()

    async def key(*args):
        return "test"

    async def base(*args):
        return "https://example.invalid/v1"

    monkeypatch.setattr(manager._key_pool, "get_key", key)
    monkeypatch.setattr(manager._key_pool, "get_base", base)
    await manager._initialize_llm_hub_models()
    cfg = manager.models["llm_hub/gpt-6-astra"]
    adapter = manager.model_clients[cfg.model_name]
    assert cfg.explicit_prompt_cache and adapter.explicit_prompt_cache
    assert not cfg.supports_sampling and not adapter.supports_sampling
    assert cfg.reasoning_efforts == adapter.reasoning_efforts
    assert adapter.reasoning["context"] == "all_turns"


def test_gpt6_catalog_is_conservative_and_has_priced_tool_safe_fallback():
    entries = llm_hub_models(max_tokens=512, default_temperature=0.7, default_timeout=30)
    entry = next(m for m in entries["response"] if m["model_id"] == "gpt-6-astra")
    assert entry["native_compaction"]
    assert entry["fallback_model"] == "llm_hub/gpt-5.6-sol"
    assert not entry.get("native_multi_agent")
    assert entry["native_programmatic_tool_calling"]
    assert entry["native_configuration_updates"]
    assert entry["cost"]["input"] == 10 / 1_000_000


def test_repeated_folding_replaces_summary_instead_of_accumulating_it():
    from agentevolver.agent.context.conversation import Conversation
    c = Conversation(task="goal")
    c.checkpoint = CompactionMessage(content="obsolete fact")
    c.append(AssistantMessage(content="new observation"))
    assert c.fold("corrected fact", keep_turns=0) == 1
    assert "obsolete fact" not in c.checkpoint.text
    assert "corrected fact" in c.checkpoint.text


@pytest.mark.asyncio
async def test_sequential_feature_rejections_each_get_a_bounded_retry(monkeypatch):
    import asyncio
    from agentevolver.model.context import ModelContextManager
    from agentevolver.model.types import ModelConfig, ModelContext

    class Unsupported(RuntimeError):
        status_code = 400

    calls = []

    async def create(**params):
        calls.append(params)
        if params.get("prompt_cache_options"):
            raise Unsupported("unknown prompt_cache_options")
        if (params.get("reasoning") or {}).get("context"):
            raise Unsupported("unsupported reasoning.context all_turns")
        return {"status": "completed", "output": [
            {"type": "message", "content": [{"text": "ok"}]},
        ]}

    c = client(explicit_prompt_cache=True, reasoning={"effort": "low", "context": "all_turns"})
    c._client = lambda: SimpleNamespace(responses=SimpleNamespace(create=create))
    manager = ModelContextManager()
    manager.models["test"] = ModelConfig(
        model_name="test", model_id="gpt-6-astra", model_type="responses", provider="llm_hub",
        reasoning={"effort": "low", "context": "all_turns"}, explicit_prompt_cache=True,
    )
    manager.model_clients["test"] = c
    monkeypatch.setattr("agentevolver.model.context._record_request_snapshot", lambda **kw: asyncio.sleep(0))
    result = await manager(name="test", input={"messages": [HumanMessage(content="hello", cache=True)],
                                              "max_retries": 1}, ctx=ModelContext(id="probe"))
    assert result.success
    assert len(calls) == 3
    assert c._disabled_features == {"prompt_cache", "persisted_reasoning"}
    assert calls[-1]["input"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_transient_or_auth_errors_do_not_disable_features(status):
    from agentevolver.model.llm_hub.response import _rejected_features
    error = RuntimeError("prompt_cache unavailable")
    error.status_code = status
    assert _rejected_features(error, ["prompt_cache"]) == []
