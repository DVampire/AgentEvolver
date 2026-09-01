"""A model request is recorded as versioned, secret-safe, content-addressed evidence.

Messages in a trajectory do not identify the action space or provider route that produced
the target. Without these checks a request snapshot can look complete while omitting tool
schemas, change identity nondeterministically, or leak the API credential it was designed
to keep out of training artifacts.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentevolver.message import HumanMessage
from agentevolver.model.context import ModelContextManager, _record_request_snapshot
from agentevolver.model.server import ModelManagerServer
from agentevolver.model.types import ModelConfig, ModelContext
from agentevolver.response import Response, ResponseType
from agentevolver.trace.request import REQUEST_SNAPSHOT_VERSION, RequestSnapshot
from agentevolver.trace.types import (
    TRACE_FORMAT_VERSION,
    TraceEventType,
    UnsupportedTraceEvent,
    model_request_event,
    parse_trace_event,
)


def _config(**updates):
    values = {
        "model_name": "main",
        "model_type": "chat/completions",
        "model_id": "provider/model-v1",
        "provider": "provider",
        "api_base": "https://user:secret@example.invalid/v1?token=hidden",
        "api_key": "must-never-appear",
        "temperature": None,
        "max_completion_tokens": 4096,
    }
    values.update(updates)
    return ModelConfig(**values)


def _snapshot(**updates):
    values = {
        "requested_model": "main",
        "routed_model": "main",
        "model_config": _config(),
        "client": SimpleNamespace(temperature=0.2, base_url="https://example.invalid/v1"),
        "messages": [HumanMessage(content="solve this")],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
        "response_format": {"type": "json_object"},
        "request_input": {"max_retries": 3},
        "call_kwargs": {},
        "stream": True,
    }
    values.update(updates)
    return RequestSnapshot.capture(**values)


def test_the_same_effective_request_has_the_same_content_identity():
    """A snapshot id is a lineage key, so process-local randomness would make it useless."""
    assert _snapshot().snapshot_id == _snapshot().snapshot_id


def test_changing_the_tool_schema_changes_the_snapshot_identity():
    """Tool schemas define the action space even when every prompt message is unchanged."""
    first = _snapshot()
    second = _snapshot(tools=[{"type": "function", "function": {"name": "read_file"}}])
    assert first.snapshot_id != second.snapshot_id


def test_compaction_protocol_parameters_are_part_of_the_snapshot_identity():
    normal = _snapshot()
    compact = _snapshot(
        request_input={
            "operation": "compact",
            "betas": ["compact-2026-01-12"],
            "context_management": {"edits": [{"type": "compact_20260112"}]},
        }
    )

    assert compact.parameters["operation"] == "compact"
    assert compact.parameters["betas"] == ["compact-2026-01-12"]
    assert compact.parameters["context_management"]["edits"][0]["type"] == "compact_20260112"
    assert compact.snapshot_id != normal.snapshot_id


def test_snapshot_records_context_layer_without_putting_it_on_the_wire_model():
    message = HumanMessage(content="task", context_layer="fixed")

    snapshot = _snapshot(messages=[message])

    assert snapshot.messages[0]["context_layer"] == "fixed"
    assert "context_layer" not in message.model_dump()


def test_the_snapshot_keeps_effective_values_without_credentials_or_endpoint_text():
    """Reproducibility needs route identity, not a reusable secret or deployment URL."""
    snapshot = _snapshot()
    payload = snapshot.model_dump_json()

    assert snapshot.schema_version == REQUEST_SNAPSHOT_VERSION
    assert snapshot.parameters["temperature"] == 0.2  # client default became effective
    assert snapshot.provider_model == "provider/model-v1"
    assert snapshot.endpoint_fingerprint.startswith("sha256:")
    assert "must-never-appear" not in payload
    assert "user:secret" not in payload
    assert "token=hidden" not in payload


def test_a_request_event_is_non_ignorable_and_carries_the_trace_envelope_version():
    """Skipping an unknown request event would silently sever training provenance."""
    event = model_request_event(
        "session-1",
        _snapshot(),
        task_id="task-1",
        agent_name="agent",
        step_number=2,
    )
    assert event.event_type is TraceEventType.MODEL_REQUEST
    assert event.schema_version == TRACE_FORMAT_VERSION
    assert event.ignorable is False
    assert event.metadata["request_snapshot_id"] == event.input["snapshot_id"]


def test_an_unknown_ignorable_event_can_be_skipped_but_a_required_one_cannot():
    """Forward-compatible readers may skip telemetry, never missing training facts."""
    assert (
        parse_trace_event(
            {
                "schema_version": 1,
                "event_type": "future_metric",
                "ignorable": True,
            }
        )
        is None
    )
    with pytest.raises(UnsupportedTraceEvent, match="non-ignorable"):
        parse_trace_event(
            {
                "schema_version": 1,
                "event_type": "future_request",
                "ignorable": False,
            }
        )


def test_a_future_trace_envelope_is_refused_even_when_the_event_claims_ignorable():
    """A reader cannot trust compatibility flags in an envelope it does not understand."""
    with pytest.raises(UnsupportedTraceEvent, match="envelope"):
        parse_trace_event(
            {
                "schema_version": TRACE_FORMAT_VERSION + 1,
                "event_type": "future_metric",
                "ignorable": True,
            }
        )


@pytest.mark.asyncio
async def test_recording_happens_before_dispatch_and_flushes_the_trace_queue():
    """Queueing without flushing leaves the request external while its record is volatile."""
    emitted = []

    async def capture(event):
        emitted.append(event)

    with (
        patch("agentevolver.trace.server.trace_manager.emit", side_effect=capture),
        patch(
            "agentevolver.trace.server.trace_manager.flush", new=AsyncMock(return_value=True)
        ) as flush,
    ):
        snapshot_id = await _record_request_snapshot(
            session_id="session-1",
            requested_model="main",
            routed_model="fallback",
            model_config=_config(model_name="fallback"),
            client=SimpleNamespace(temperature=0.2),
            messages=[HumanMessage(content="solve this")],
            tools=[],
            response_format=None,
            request_input={
                "trace_context": {
                    "task_id": "task-1",
                    "agent_name": "agent",
                    "step_number": 4,
                },
            },
            call_kwargs={},
            stream=True,
            attempt=1,
            route_index=1,
        )

    assert snapshot_id == emitted[0].metadata["request_snapshot_id"]
    assert (emitted[0].task_id, emitted[0].step_number) == ("task-1", 4)
    assert emitted[0].input["requested_model"] == "main"
    assert emitted[0].input["routed_model"] == "fallback"
    flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_model_manager_records_before_it_calls_the_provider():
    """The helper existing is not enough; the provider boundary itself must invoke it."""
    order = []

    class Client:
        async def __call__(self, **_kwargs):
            order.append("provider")
            return Response(type=ResponseType.LLM, success=True, message="answer")

        def set_api_key(self, _key):
            pass

    async def record(**_kwargs):
        order.append("snapshot")

    manager = ModelContextManager()
    manager.models["main"] = _config()
    manager.model_clients["main"] = Client()
    with patch("agentevolver.model.context._record_request_snapshot", side_effect=record):
        result = await manager(
            name="main",
            input={"messages": [HumanMessage(content="hello")], "max_retries": 1},
            ctx=ModelContext(id="session-1"),
        )

    assert result.success is True
    assert order == ["snapshot", "provider"]


@pytest.mark.asyncio
async def test_compaction_requires_declared_route_capability_and_client_support():
    order = []

    class Client:
        @staticmethod
        def compaction_ready(_messages):
            return True

        @staticmethod
        def compaction_options():
            return {"betas": ["compact-2026-01-12"]}

        async def compact_history(self, _messages):
            order.append("provider")
            return {
                "summary": "checkpoint",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }

    async def record(**kwargs):
        order.append("snapshot")
        assert kwargs["request_input"]["betas"] == ["compact-2026-01-12"]
        return "snapshot-id"

    manager = ModelContextManager()
    manager.models["main"] = _config(provider="anthropic", native_compaction=True)
    manager.model_clients["main"] = Client()
    with patch("agentevolver.model.context._record_request_snapshot", side_effect=record):
        result = await manager.compact_history("main", [HumanMessage(content="history")])

    assert result["summary"] == "checkpoint"
    assert result["provider"] == "anthropic"
    assert order == ["snapshot", "provider"]


@pytest.mark.asyncio
async def test_model_server_exposes_native_compaction(monkeypatch):
    manager = ModelManagerServer()
    expected = {"summary": "checkpoint", "native": True}
    compact = AsyncMock(return_value=expected)
    monkeypatch.setattr(manager.model_context_manager, "compact_history", compact)
    messages = [HumanMessage(content="history")]

    result = await manager.compact_history(
        "main",
        messages,
        session_id="session-1",
        task_id="task-1",
        agent_name="meta_agent",
        step_number=23,
        max_output_tokens=None,
    )

    assert result is expected
    compact.assert_awaited_once_with(
        "main",
        messages,
        session_id="session-1",
        task_id="task-1",
        agent_name="meta_agent",
        step_number=23,
        max_output_tokens=None,
    )


@pytest.mark.asyncio
async def test_native_compaction_forwards_a_text_output_limit_to_capable_client():
    observed = {}

    class Client:
        @staticmethod
        def compaction_ready(_messages):
            return True

        @staticmethod
        def compaction_options(max_output_tokens=None):
            observed["snapshot_limit"] = max_output_tokens
            return {"max_tokens": max_output_tokens}

        async def compact_history(self, _messages, max_output_tokens=None):
            observed["provider_limit"] = max_output_tokens
            return {"summary": "checkpoint"}

    manager = ModelContextManager()
    manager.models["main"] = _config(provider="anthropic", native_compaction=True)
    manager.model_clients["main"] = Client()

    result = await manager.compact_history(
        "main", [HumanMessage(content="history")], max_output_tokens=1536,
    )

    assert result["summary"] == "checkpoint"
    assert observed == {"snapshot_limit": 1536, "provider_limit": 1536}


@pytest.mark.asyncio
async def test_a_client_method_does_not_opt_an_unverified_model_into_native_compaction():
    called = False

    class Client:
        async def compact_history(self, _messages):
            nonlocal called
            called = True
            return {"summary": "should not run"}

    manager = ModelContextManager()
    manager.models["main"] = _config(native_compaction=False)
    manager.model_clients["main"] = Client()

    assert await manager.compact_history("main", [HumanMessage(content="history")]) is None
    assert called is False


@pytest.mark.asyncio
async def test_malformed_compaction_request_does_not_disable_the_route():
    class BadRequest(RuntimeError):
        status_code = 400

    class Client:
        async def compact_history(self, _messages):
            raise BadRequest("invalid message role at input[2]")

    manager = ModelContextManager()
    manager.models["main"] = _config(native_compaction=True)
    manager.model_clients["main"] = Client()

    with pytest.raises(BadRequest):
        await manager.compact_history("main", [HumanMessage(content="history")])
    assert "compaction" not in manager._disabled_route_features.get("main", set())


@pytest.mark.asyncio
async def test_explicit_compaction_rejection_disables_only_that_route_feature():
    class Unsupported(RuntimeError):
        status_code = 400

    class Client:
        async def compact_history(self, _messages):
            raise Unsupported("context_management compaction is not supported")

    manager = ModelContextManager()
    manager.models["main"] = _config(native_compaction=True)
    manager.model_clients["main"] = Client()

    assert (
        await manager.compact_history(
            "main",
            [HumanMessage(content="history")],
        )
        is None
    )
    assert manager._disabled_route_features["main"] == {"compaction"}


@pytest.mark.asyncio
async def test_the_streaming_path_records_before_the_first_provider_event():
    """Agents use ``stream``; testing only the buffered path would leave real runs bare."""
    order = []

    class Client:
        async def stream(self, **_kwargs):
            order.append("provider")
            yield "event"

        def set_api_key(self, _key):
            pass

    async def record(**_kwargs):
        order.append("snapshot")

    manager = ModelContextManager()
    manager.models["main"] = _config()
    manager.model_clients["main"] = Client()
    with patch("agentevolver.model.context._record_request_snapshot", side_effect=record):
        events = [
            event
            async for event in manager.stream(
                name="main",
                input={"messages": [HumanMessage(content="hello")], "max_retries": 1},
                ctx=ModelContext(id="session-1"),
            )
        ]

    assert events == ["event"]
    assert order == ["snapshot", "provider"]
