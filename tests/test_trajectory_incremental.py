"""Trajectory projection resumes from committed Trace watermarks without duplicates."""

from __future__ import annotations

from agentevolver.trace.projection import ProjectionWatermarkStore
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.trajectory.projector import (
    PROJECTOR_VERSION,
    IncrementalTrajectoryProjector,
    project_trajectory,
)

SESSION = "session-incremental"
TASK = "task-incremental"


def _events():
    return [
        TraceEvent(
            event_type=TraceEventType.AGENT_START,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            input={"task": "solve"},
            seq_no=0,
        ),
        TraceEvent(
            event_type=TraceEventType.MODEL_REQUEST,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            step_number=1,
            seq_no=1,
            input={"messages": [{"role": "user", "content": "solve"}]},
            metadata={"request_snapshot_id": "sha256:request"},
        ),
        TraceEvent(
            event_type=TraceEventType.TOOL_START,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            step_number=1,
            action_index=0,
            action_type="tool",
            action_name="bash",
            input={"command": "pwd"},
            seq_no=2,
            metadata={"call_id": "call-1"},
        ),
        TraceEvent(
            event_type=TraceEventType.TOOL_CALL,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            step_number=1,
            action_index=0,
            action_type="tool",
            action_name="bash",
            output="/workspace",
            success=True,
            seq_no=3,
            metadata={"call_id": "call-1"},
        ),
        TraceEvent(
            event_type=TraceEventType.AGENT_CALL,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            step_number=1,
            reasoning="done",
            success=True,
            seq_no=4,
        ),
        TraceEvent(
            event_type=TraceEventType.AGENT_END,
            session_id=SESSION,
            task_id=TASK,
            agent_name="agent",
            output="answer",
            success=True,
            seq_no=5,
        ),
    ]


class GrowingReader:
    def __init__(self, events):
        self.events = list(events)
        self.requests = []

    def read_from(self, session_id, *, after_seq=-1, limit=None, **_kwargs):
        self.requests.append(after_seq)
        selected = [
            event.to_dict()
            for event in self.events
            if event.session_id == session_id and event.seq_no > after_seq
        ]
        return selected if limit is None else selected[:limit]


def test_incremental_projector_resumes_and_matches_a_full_rebuild(tmp_path):
    events = _events()
    reader = GrowingReader(events[:5])
    projector = IncrementalTrajectoryProjector(reader, str(tmp_path / "trace"))

    partial = projector.project(SESSION, task_id=TASK, batch_size=2)
    assert partial.success is False
    assert reader.requests == [-1, 1, 3]

    reader.requests.clear()
    reader.events.append(events[5])
    resumed = projector.project(SESSION, task_id=TASK, batch_size=2)

    assert reader.requests == [4]
    assert resumed.model_dump() == project_trajectory(events, task_id=TASK).model_dump()
    watermark = ProjectionWatermarkStore(str(tmp_path / "trace")).load(
        "trajectory",
        PROJECTOR_VERSION,
        SESSION,
    )
    assert watermark.last_seq == 5

    state = projector._load_state(SESSION)
    assert [event["seq_no"] for event in state.events] == list(range(6))


def test_explicit_rebuild_discards_cursor_and_replays_from_the_start(tmp_path):
    reader = GrowingReader(_events())
    projector = IncrementalTrajectoryProjector(reader, str(tmp_path / "trace"))
    projector.project(SESSION, task_id=TASK)
    reader.requests.clear()

    rebuilt = projector.project(SESSION, task_id=TASK, rebuild=True)

    assert rebuilt.success is True
    assert reader.requests[0] == -1


def test_torn_state_tail_is_repaired_before_a_resumed_append(tmp_path):
    events = _events()
    reader = GrowingReader(events[:5])
    projector = IncrementalTrajectoryProjector(reader, str(tmp_path / "trace"))
    projector.project(SESSION, task_id=TASK)
    with open(projector._state_path(SESSION), "a", encoding="utf-8") as handle:
        handle.write('{"type":"event"')

    reader.events.append(events[5])
    resumed = IncrementalTrajectoryProjector(
        reader,
        str(tmp_path / "trace"),
    ).project(SESSION, task_id=TASK)

    assert resumed.success is True
    assert (
        IncrementalTrajectoryProjector(
            reader,
            str(tmp_path / "trace"),
        )
        .project(SESSION, task_id=TASK)
        .success
        is True
    )
