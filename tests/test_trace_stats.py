from __future__ import annotations

import pytest

from agentevolver.trace.projection import (
    ProjectionRegistrationError,
    ProjectionRegistry,
    get_default_projection_registry,
)
from agentevolver.trace.stats import TraceStatsProjector
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_call_event,
    agent_end_event,
    model_request_event,
    tool_call_event,
)


class Reader:
    def __init__(self, events):
        self.events = events

    def read_from(self, session_id, *, after_seq=-1, limit=None):
        found = [
            event for event in self.events
            if event.get("session_id") == session_id and int(event["seq_no"]) > after_seq
        ]
        return found[:limit]


class Snapshot:
    snapshot_id = "snapshot-1"
    routed_model = "route-a"

    def model_dump(self, mode="json"):
        return {
            "snapshot_id": self.snapshot_id,
            "requested_model": "primary",
            "routed_model": self.routed_model,
            "provider": "openai",
            "provider_model": "gpt-test",
            "messages": [],
            "tools": [],
            "parameters": {},
        }


def payloads(*events: TraceEvent):
    result = []
    for seq, event in enumerate(events):
        result.append(event.model_copy(update={"seq_no": seq}).to_dict())
    return result


def test_stats_projector_aggregates_without_double_counting_run_usage(tmp_path):
    request = model_request_event("s1", Snapshot(), task_id="t1", agent_name="a1")
    step = agent_call_event(
        "s1", "t1", "a1", 1, duration_ms=12,
        usage={"input_tokens": 10, "output_tokens": 3, "cost": 0.25},
    )
    tool = tool_call_event(
        "s1", "t1", "a1", 1, 0, "bash", "ok", True, duration_ms=5,
    )
    tool.metadata["execution"] = {"tool_name": "bash", "stage": "finalize"}
    end = agent_end_event(
        "s1", "t1", "a1", True, "done", duration_ms=20,
        usage={"input_tokens": 10, "output_tokens": 3, "cost": 0.25},
    )
    projector = TraceStatsProjector(Reader(payloads(request, step, tool, end)), tmp_path)

    stats = projector.project("s1", batch_size=2)

    assert stats.event_count == 4
    assert stats.event_counts == {
        "agent_call": 1, "agent_end": 1, "model_request": 1, "tool_call": 1,
    }
    assert stats.model_routes == {"route-a": 1}
    assert stats.providers == {"openai": 1}
    assert stats.tool_calls == {"bash": 1}
    assert stats.successful_actions == 1
    assert stats.usage.input_tokens == 10
    assert stats.usage.output_tokens == 3
    assert stats.usage.cost == pytest.approx(0.25)
    assert stats.reported_run_usage is not None
    assert stats.reported_run_usage.input_tokens == 10
    assert stats.terminal_success is True
    assert stats.source_last_seq == 3


def test_stats_checkpoint_ahead_of_watermark_is_reconciled_without_double_count(tmp_path):
    events = payloads(agent_call_event(
        "s1", "t1", "a1", 1, usage={"input_tokens": 7, "output_tokens": 2},
    ))
    reader = Reader(events)
    projector = TraceStatsProjector(reader, tmp_path)
    first = projector.project("s1")
    assert first.event_count == 1

    # Simulate a crash after the state rename and before watermark replacement.
    projector.watermarks.reset("stats", "s1")
    reader.events.extend(payloads(TraceEvent(
        event_type=TraceEventType.ERROR, session_id="s1", error="boom",
    )))
    reader.events[-1]["seq_no"] = 1

    resumed = TraceStatsProjector(reader, tmp_path).project("s1")
    assert resumed.event_count == 2
    assert resumed.usage.input_tokens == 7
    assert resumed.error_events == 1


def test_stats_projector_advances_past_ignorable_unknown_event(tmp_path):
    raw = {
        "schema_version": 1,
        "ignorable": True,
        "id": "future-1",
        "event_type": "future_observation",
        "session_id": "s1",
        "seq_no": 0,
    }
    projector = TraceStatsProjector(Reader([raw]), tmp_path)
    stats = projector.project("s1")
    assert stats.event_count == 0
    assert stats.source_last_seq == 0


def test_projection_registry_validates_identity_and_duplicates(tmp_path):
    registry = ProjectionRegistry()
    registry.register("stats", 1, TraceStatsProjector)
    assert registry.names() == ("stats",)
    assert isinstance(registry.create("stats", Reader([]), tmp_path), TraceStatsProjector)
    with pytest.raises(ProjectionRegistrationError, match="already registered"):
        registry.register("stats", 1, TraceStatsProjector)
    with pytest.raises(ProjectionRegistrationError, match="must bump version"):
        registry.register("stats", 1, TraceStatsProjector, replace=True)


def test_default_projection_registry_exposes_builtin_consumers():
    assert get_default_projection_registry().names() == ("stats", "trajectory")
