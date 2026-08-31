"""Semantic durability profiles never confuse queue drainage with complete evidence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.config.validate import validate_assembly
from agentevolver.message import HumanMessage
from agentevolver.model.context import ModelContextManager
from agentevolver.model.types import ModelConfig, ModelContext
from agentevolver.utils import AsyncQueue
from agentevolver.response import Response, ResponseType
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.types import Tool, ToolContext
from agentevolver.trace.checkpoint import (
    TraceCheckpointBoundary,
    TraceIntegrityError,
    TraceIntegrityProfile,
    checkpoint_trace,
    resolve_integrity_profile,
)
from agentevolver.trace.persistence import SQLiteTracePersistence
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.trace.writer import TraceWriter


@pytest.fixture
def isolated_trace_manager():
    """Borrow and fully restore the process-wide Trace singleton."""
    from agentevolver.trace.server import trace_manager

    names = (
        "_running", "_queue", "_writer", "_log_root", "_dropped_events",
        "_reported_integrity_gaps", "_next_seq", "_surface", "_events",
    )
    saved = {name: getattr(trace_manager, name) for name in names}
    trace_manager._running = False
    trace_manager._queue = None
    trace_manager._writer = None
    trace_manager._dropped_events = {}
    trace_manager._reported_integrity_gaps = set()
    trace_manager._next_seq = {}
    trace_manager._surface = {}
    trace_manager._events = {}
    yield trace_manager
    for name, value in saved.items():
        setattr(trace_manager, name, value)


def test_integrity_profiles_are_explicit_and_unknown_values_are_refused():
    assert resolve_integrity_profile("best_effort") is TraceIntegrityProfile.INTERACTIVE
    assert resolve_integrity_profile("strict") is TraceIntegrityProfile.TRAINING
    assert resolve_integrity_profile("high-risk") is TraceIntegrityProfile.HIGH_RISK
    with pytest.raises(ValueError, match="unknown trace integrity profile"):
        resolve_integrity_profile("train-ish")


def test_config_validation_refuses_an_unknown_integrity_profile():
    config = SimpleNamespace(trace_integrity_profile="train-ish")
    problems = validate_assembly(config)
    assert problems == [
        "trace_integrity_profile must be one of: interactive, training, high_risk "
        "(got 'train-ish')"
    ]
    with pytest.raises(ValueError, match="trace_integrity_profile"):
        validate_assembly(config, strict=True)


@pytest.mark.parametrize("profile", ["interactive", "training", "high_risk"])
def test_config_validation_accepts_supported_integrity_profiles(profile):
    assert validate_assembly(
        SimpleNamespace(trace_integrity_profile=profile), strict=True,
    ) == []


def _agent() -> Agent:
    agent = Agent.model_construct(name="probe", description="probe", metadata={})
    agent.use_memory = False
    agent.memory_name = None
    agent.constraints = []
    agent.model_name = "main"
    agent.max_actions = 10
    object.__setattr__(agent, "_pending_step_tokens", {})
    return agent


@pytest.mark.asyncio
async def test_training_profile_requires_a_running_trace_writer(isolated_trace_manager):
    emitted = AsyncMock(return_value=False)
    isolated_trace_manager.emit = emitted
    try:
        with pytest.raises(TraceIntegrityError, match="not running"):
            await checkpoint_trace(
                "training-session",
                TraceCheckpointBoundary.MODEL_REQUEST,
                profile="training",
            )
    finally:
        # Instance assignment shadows the class method; remove it before restoring the
        # singleton for unrelated tests.
        isolated_trace_manager.__dict__.pop("emit", None)
    event = emitted.await_args.args[0]
    assert event.metadata["type"] == "integrity_degraded"
    assert event.ignorable is False


@pytest.mark.asyncio
async def test_interactive_timeout_records_one_degradation_and_continues(
    isolated_trace_manager,
):
    isolated_trace_manager._running = True
    isolated_trace_manager._queue = AsyncQueue(maxsize=4)
    flush = AsyncMock(return_value=False)
    emit = AsyncMock(return_value=True)
    with (
        patch.object(isolated_trace_manager, "flush", flush),
        patch.object(isolated_trace_manager, "emit", emit),
    ):
        first = await checkpoint_trace(
            "interactive-session",
            TraceCheckpointBoundary.EXTERNAL_EFFECT,
            profile="interactive",
            metadata={"tool_name": "write_file_tool"},
        )
        second = await checkpoint_trace(
            "interactive-session",
            TraceCheckpointBoundary.EXTERNAL_EFFECT,
            profile="interactive",
            metadata={"tool_name": "write_file_tool"},
        )
    assert first is False and second is False
    assert emit.await_count == 1
    event = emit.await_args.args[0]
    assert event.metadata["boundary"] == "before_external_effect"
    assert event.metadata["tool_name"] == "write_file_tool"


@pytest.mark.asyncio
async def test_queue_overflow_permanently_marks_the_session_incomplete(
    isolated_trace_manager, tmp_path,
):
    class Writer:
        def next_seq(self, session_id):
            return 0

        def durability_error(self, session_id):
            return None

    isolated_trace_manager._running = True
    isolated_trace_manager._log_root = str(tmp_path)
    isolated_trace_manager._queue = AsyncQueue(maxsize=1)
    isolated_trace_manager._writer = Writer()
    first = await isolated_trace_manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s1", label="first",
    ))
    second = await isolated_trace_manager.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="s1", label="dropped",
    ))
    assert first is True and second is False
    assert [event.label for event in isolated_trace_manager.events("s1")] == ["first"]
    assert "dropped 1 event" in isolated_trace_manager.integrity_issue("s1")

    with patch.object(isolated_trace_manager, "flush", AsyncMock(return_value=True)):
        with pytest.raises(TraceIntegrityError, match="dropped 1 event"):
            await checkpoint_trace(
                "s1", TraceCheckpointBoundary.STEP_END, profile="training",
            )

    # The gap is a durable invalidation record, not merely process memory. A restarted
    # manager that points at the same Trace root must still refuse this Session.
    isolated_trace_manager._dropped_events = {}
    assert "dropped 1 event" in isolated_trace_manager.integrity_issue("s1")


@pytest.mark.asyncio
@pytest.mark.parametrize("writer_type", [TraceWriter, SQLiteTracePersistence])
async def test_persistence_write_failure_survives_a_drained_queue(
    writer_type, tmp_path,
):
    queue = AsyncQueue(maxsize=4)
    writer = writer_type(str(tmp_path), queue)
    writer._write_event = Mock(side_effect=OSError("disk unavailable"))
    queue.emit(TraceEvent(
        event_type=TraceEventType.CUSTOM, session_id="broken", label="lost",
    ))
    await queue.stop()
    await writer._run()

    assert writer.durability_error("broken") == "disk unavailable"


@pytest.mark.asyncio
async def test_model_provider_is_not_called_when_training_snapshot_cannot_commit(
    isolated_trace_manager,
):
    calls = 0

    class Client:
        async def __call__(self, **kwargs):
            nonlocal calls
            calls += 1
            return Response(type=ResponseType.LLM, success=True, message="should not run")

        def set_api_key(self, key):
            pass

    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main", model_type="chat/completions", model_id="test-model",
        provider="test",
    )
    manager.model_clients["main"] = Client()

    with pytest.raises(TraceIntegrityError, match="before_model_request"):
        await manager(
            name="main",
            input={
                "messages": [HumanMessage(content="train me")],
                "trace_integrity_profile": "training",
                "max_retries": 3,
            },
            ctx=ModelContext(id="training-session"),
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_direct_training_model_call_requires_a_real_session_id():
    calls = 0

    class Client:
        async def __call__(self, **kwargs):
            nonlocal calls
            calls += 1
            return Response(type=ResponseType.LLM, success=True, message="not allowed")

        def set_api_key(self, key):
            pass

    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main", model_type="chat/completions", model_id="test-model",
        provider="test",
    )
    manager.model_clients["main"] = Client()

    with pytest.raises(TraceIntegrityError, match="requires a real session id"):
        await manager(
            name="main",
            input={
                "messages": [HumanMessage(content="train me")],
                "trace_integrity_profile": "training",
            },
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_mutating_tool_is_blocked_but_read_only_tool_is_not(
    isolated_trace_manager,
):
    called = []

    class ProbeTool(Tool):
        name: str = "probe_tool"
        description: str = "Probe the post-policy checkpoint."

        async def __call__(self, **kwargs):
            called.append(self.mutates)
            return Response(type=ResponseType.TOOL, success=True, message="ran")

    async def invoke(mutates):
        tool = ProbeTool(mutates=mutates)
        manager = ToolContextManager()

        async def get_info(name):
            return SimpleNamespace(version="1.0.0", instance=tool)

        manager.get_info = get_info
        return await manager(
            name="probe_tool", input={},
            ctx=ToolContext(
                id="strict-tool-session", name="probe",
                extra={"trace_integrity_profile": "high_risk"},
            ),
        )

    with pytest.raises(TraceIntegrityError, match="before_external_effect"):
        await invoke(True)
    assert called == []

    response = await invoke(False)
    assert response.success is True
    assert called == [False]


def test_approval_timeout_config_must_be_positive():
    config = SimpleNamespace(approval_timeout_seconds=0)
    assert validate_assembly(config) == [
        "approval_timeout_seconds must be a positive number (got 0)"
    ]
    with pytest.raises(ValueError, match="approval_timeout_seconds"):
        validate_assembly(config, strict=True)


@pytest.mark.asyncio
async def test_strict_checkpoint_requires_a_real_session_id(isolated_trace_manager):
    isolated_trace_manager._running = True
    isolated_trace_manager._queue = AsyncQueue(maxsize=1)
    with pytest.raises(TraceIntegrityError, match="requires a real session id"):
        await checkpoint_trace(
            "", TraceCheckpointBoundary.EXTERNAL_EFFECT, profile="high_risk",
        )


@pytest.mark.asyncio
async def test_post_step_flushes_after_all_step_hooks():
    agent = _agent()
    ctx = AgentContext(id="step-session", extra={"trace_integrity_profile": "training"})
    hooks = AsyncMock(return_value=SimpleNamespace())
    checkpoint = AsyncMock(return_value=True)
    with (
        patch("agentevolver.hook.server.hook_manager", hooks),
        patch("agentevolver.trace.checkpoint.checkpoint_trace", checkpoint),
    ):
        await agent._post_step(
            "task-1", 4, ctx, [],
            reasoning="reason", plan=[], step_tokens=3, done=False,
            step_usage={"output_tokens": 3},
        )

    # trace_hook forwards its numbered event to memory; trajectory runs after it.
    assert hooks.await_count == 2
    checkpoint.assert_awaited_once()
    args, kwargs = checkpoint.await_args
    assert args[:2] == ("step-session", TraceCheckpointBoundary.STEP_END)
    assert kwargs["ctx"] is ctx
    assert kwargs["metadata"]["step_number"] == 4


@pytest.mark.asyncio
async def test_agent_propagates_integrity_profile_to_the_model_boundary():
    from agentevolver.model import model_manager

    agent = _agent()
    ctx = AgentContext(
        id="model-session", extra={"trace_integrity_profile": "training"},
    )
    captured = []

    async def stream(**kwargs):
        captured.append(kwargs)
        if False:
            yield None

    with (
        patch("agentevolver.hook.server.hook_manager", AsyncMock()),
        patch(
            "agentevolver.agent.native_tools.assemble_native_tools",
            AsyncMock(return_value=([], {})),
        ),
        patch.object(model_manager.model_context_manager, "stream", stream),
    ):
        await agent._think([], "task-1", 2, ctx)

    assert captured[0]["input"]["trace_integrity_profile"] == "training"
    assert captured[0]["input"]["trace_context"] == {
        "task_id": "task-1", "agent_name": "probe", "step_number": 2,
    }
