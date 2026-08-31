"""Provider capability observations are scoped, expiring, and visible to callers.

A relay rejection must temporarily disable only the affected route, then allow a later
probe instead of becoming permanent global state. Snapshots expose requested and actual
features without leaking endpoint URLs.
"""

from types import SimpleNamespace

import pytest

from agentevolver.model.capabilities import (
    CapabilityRoute,
    CapabilityState,
    ProviderCapabilityRegistry,
)
from agentevolver.model.context import ModelContextManager
from agentevolver.model.llm_hub.response import ResponseLLMHub
from agentevolver.model.types import ModelConfig


def _route(model_id="provider/model-v1"):
    return CapabilityRoute.capture(
        ModelConfig(
            model_name="main",
            model_id=model_id,
            model_type="responses",
            provider="relay",
            api_base="https://relay.invalid/v1",
            output_version="2026-08-01",
        )
    )


def test_capability_rejection_is_scoped_and_expires_for_a_reprobe():
    now = [100.0]
    registry = ProviderCapabilityRegistry(ttl_seconds=10, clock=lambda: now[0])
    route = _route()

    registry.observe(route, "multi_agent", CapabilityState.REJECTED, "not enabled")
    assert registry.allows(route, "multi_agent") is False
    assert registry.snapshot(route, ["multi_agent"])["multi_agent"]["last_error"] == "not enabled"

    now[0] = 111.0
    assert registry.allows(route, "multi_agent") is True
    assert registry.consume_expired(route, "multi_agent") is True
    assert registry.consume_expired(route, "multi_agent") is False


def test_capability_key_does_not_leak_the_endpoint():
    route = _route()
    assert route.endpoint_fingerprint.startswith("sha256:")
    assert "relay.invalid" not in route.endpoint_fingerprint
    assert route.api_version == "2026-08-01"


def test_capability_observations_survive_registry_restart(tmp_path):
    path = tmp_path / "provider-capabilities.json"
    now = [100.0]
    first = ProviderCapabilityRegistry(
        ttl_seconds=30, clock=lambda: now[0], persist_path=str(path),
    )
    route = _route()
    first.observe(route, "multi_agent", CapabilityState.REJECTED, "not enabled")

    restored = ProviderCapabilityRegistry(
        ttl_seconds=30, clock=lambda: now[0], persist_path=str(path),
    )

    assert restored.state(route, "multi_agent") is CapabilityState.REJECTED
    assert restored.snapshot(route, ["multi_agent"])["multi_agent"]["attempts"] == 1
    payload = path.read_text(encoding="utf-8")
    assert "relay.invalid" not in payload


def test_stale_registry_instances_merge_observations_instead_of_clobbering(tmp_path):
    """Each Gateway may have loaded the cache before another process records a probe."""
    path = tmp_path / "provider-capabilities.json"
    first = ProviderCapabilityRegistry(persist_path=str(path))
    stale_second = ProviderCapabilityRegistry(persist_path=str(path))

    first.observe(_route("provider/model-a"), "multi_agent", CapabilityState.VERIFIED)
    stale_second.observe(
        _route("provider/model-b"), "compaction", CapabilityState.REJECTED,
    )

    restored = ProviderCapabilityRegistry(persist_path=str(path))
    assert restored.state(
        _route("provider/model-a"), "multi_agent",
    ) is CapabilityState.VERIFIED
    assert restored.state(
        _route("provider/model-b"), "compaction",
    ) is CapabilityState.REJECTED


def test_expired_persisted_observations_are_not_restored(tmp_path):
    path = tmp_path / "provider-capabilities.json"
    now = [100.0]
    first = ProviderCapabilityRegistry(
        ttl_seconds=10, clock=lambda: now[0], persist_path=str(path),
    )
    first.observe(_route(), "multi_agent", CapabilityState.VERIFIED)
    now[0] = 111.0

    restored = ProviderCapabilityRegistry(clock=lambda: now[0], persist_path=str(path))

    assert restored.state(_route(), "multi_agent") is CapabilityState.UNKNOWN


def test_request_contract_exposes_requested_actual_and_probe_state():
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="model",
        model_type="responses",
        provider="relay",
        api_base="https://relay.invalid/v1",
        supports_functions=True,
        native_multi_agent=True,
    )
    client = ResponseLLMHub(model="model", provider_name="relay")
    manager.model_clients["main"] = client

    _, snapshot = manager._runtime_call_kwargs(
        "main",
        client,
        {"runtime_features": {"multi_agent": True}},
        {},
    )
    manager._observe_capability_attempt("main", client, snapshot)
    resolution = snapshot["capability_resolution"]

    assert resolution["requested"] == {"multi_agent": True}
    assert resolution["actual"]["multi_agent"] == "native"
    assert resolution["status"]["multi_agent"]["state"] == "probing"


def test_only_features_encoded_on_the_wire_receive_observations():
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_id="model",
        model_type="responses",
        provider="relay",
        supports_functions=True,
        native_programmatic_tool_calling=True,
    )
    client = ResponseLLMHub(
        model="model",
        provider_name="relay",
        native_programmatic_tool_calling=True,
    )
    manager.model_clients["main"] = client

    _, without_program = manager._runtime_call_kwargs(
        "main",
        client,
        {"runtime_features": {"programmatic_tool_calling": True}},
        {},
        tools=[SimpleNamespace(metadata={})],
    )
    manager._observe_capability_attempt("main", client, without_program)
    route = CapabilityRoute.capture(manager.models["main"], client)
    assert manager.capability_registry.state(
        route, "programmatic_tool_calling",
    ) is CapabilityState.UNKNOWN

    _, with_program = manager._runtime_call_kwargs(
        "main",
        client,
        {"runtime_features": {"programmatic_tool_calling": True}},
        {},
        tools=[SimpleNamespace(metadata={"programmatic": True})],
    )
    manager._observe_capability_attempt("main", client, with_program)
    assert manager.capability_registry.state(
        route, "programmatic_tool_calling",
    ) is CapabilityState.PROBING


def test_per_invocation_reasoning_effort_reaches_wire_and_snapshot():
    manager = ModelContextManager()
    responses_config = ModelConfig(
        model_name="responses",
        model_id="model",
        model_type="responses",
        provider="relay",
    )
    responses_client = ResponseLLMHub(model="model", provider_name="relay")
    manager.models["responses"] = responses_config
    manager.model_clients["responses"] = responses_client

    wire, snapshot = manager._runtime_call_kwargs(
        "responses",
        responses_client,
        {"reasoning_effort": "medium"},
        {},
    )
    assert wire["reasoning"] == {"effort": "medium"}
    assert snapshot["reasoning_effort"] == "medium"

    chat_config = ModelConfig(
        model_name="chat",
        model_id="gpt",
        model_type="chat/completions",
        provider="openai",
    )
    chat_client = SimpleNamespace(provider="openai", base_url=None, _disabled_features=set())
    manager.models["chat"] = chat_config
    manager.model_clients["chat"] = chat_client
    wire, snapshot = manager._runtime_call_kwargs(
        "chat",
        chat_client,
        {"reasoning_effort": "low"},
        {},
    )
    assert wire["reasoning_effort"] == "low"
    assert snapshot["reasoning_effort"] == "low"


def test_expired_rejection_clears_adapter_suppression():
    now = [100.0]
    manager = ModelContextManager()
    manager.capability_registry = ProviderCapabilityRegistry(
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    config = ModelConfig(
        model_name="main",
        model_id="model",
        model_type="responses",
        provider="relay",
        supports_functions=True,
        native_programmatic_tool_calling=True,
    )
    client = ResponseLLMHub(
        model="model",
        provider_name="relay",
        native_programmatic_tool_calling=True,
    )
    manager.models["main"] = config
    manager.model_clients["main"] = client
    route = CapabilityRoute.capture(config, client)
    manager.capability_registry.observe(
        route,
        "programmatic_tool_calling",
        CapabilityState.REJECTED,
    )
    client._disabled_features.add("programmatic_tool_calling")

    now[0] = 111.0
    manager._runtime_call_kwargs(
        "main",
        client,
        {"runtime_features": {"programmatic_tool_calling": True}},
        {},
    )
    assert "programmatic_tool_calling" not in client._disabled_features


@pytest.mark.asyncio
async def test_expired_compaction_rejection_reenables_native_probe():
    now = [100.0]
    manager = ModelContextManager()
    manager.capability_registry = ProviderCapabilityRegistry(
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    config = ModelConfig(
        model_name="main",
        model_id="model",
        model_type="responses",
        provider="relay",
        native_compaction=True,
    )

    class Client:
        provider = "relay"
        model = "model"
        base_url = None

        @staticmethod
        def compaction_ready(_messages):
            return True

        @staticmethod
        async def compact_history(_messages):
            return {"items": [{"type": "compaction", "id": "cmp_1"}]}

    client = Client()
    manager.models["main"] = config
    manager.model_clients["main"] = client
    route = CapabilityRoute.capture(config, client)
    manager.capability_registry.observe(
        route,
        "compaction",
        CapabilityState.REJECTED,
        "not enabled",
    )
    manager._disabled_route_features["main"] = {"compaction"}

    assert await manager.compact_history("main", []) is None
    now[0] = 111.0
    result = await manager.compact_history("main", [])

    assert result["items"][0]["id"] == "cmp_1"
    assert "compaction" not in manager._disabled_route_features["main"]
    assert manager.capability_registry.state(route, "compaction") is CapabilityState.VERIFIED
