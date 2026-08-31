from pathlib import Path

import pytest

from agentevolver.memory.default.tiered import TieredMemory
from agentevolver.trace.server import TraceManager
from agentevolver.trace.types import (
    agent_call_event,
    agent_start_event,
    tool_call_event,
    TraceEvent,
    TraceEventType,
)
from agentevolver.trace.surface import replace_op
from agentevolver.trace.writer import TraceWriter
from agentevolver.utils import AsyncQueue


def _manager(tmp_path, events):
    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)
    manager._writer = TraceWriter(str(tmp_path), AsyncQueue())
    for event in events:
        path = Path(manager._writer._session_path("s"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            import json
            handle.write(json.dumps(event.to_dict(), default=str) + "\n")
    return manager


def test_trace_rehydrate_restores_replaced_surface(tmp_path):
    start = agent_start_event("s", "t", "meta_agent", "fix it")
    start.seq_no = 0
    step = agent_call_event("s", "t", "meta_agent", 1, assistant_text="working")
    step.seq_no = 1
    checkpoint = TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        seq_no=2,
        message="checkpoint",
        metadata={"type": "compaction"},
        surface_op=replace_op(0, 1),
        source_event_seqs=[0, 1],
    )
    tail = tool_call_event("s", "t", "meta_agent", 2, 0, "bash", "ok", True)
    tail.seq_no = 3
    manager = _manager(tmp_path, [start, step, checkpoint, tail])

    assert [event.seq_no for event in manager.rehydrate("s")] == [0, 1, 2, 3]
    assert manager.surface("s") == [2, 3]
    assert manager._next_seq["s"] == 4


def test_trace_rehydrate_rejects_conflicting_sequence(tmp_path):
    first = agent_start_event("s", "t", "meta_agent", "one")
    second = agent_start_event("s", "t", "meta_agent", "two")
    first.seq_no = second.seq_no = 0
    manager = _manager(tmp_path, [first, second])

    with pytest.raises(ValueError, match="conflicting events"):
        manager.rehydrate("s")


def test_trace_rehydrate_rejects_history_over_retention_limit(tmp_path):
    events = []
    for seq in range(3):
        event = agent_call_event("s", "t", "meta_agent", seq, assistant_text=str(seq))
        event.seq_no = seq
        events.append(event)
    manager = _manager(tmp_path, events)
    manager._max_retained = 2

    with pytest.raises(RuntimeError, match="exceeding the rehydrate limit"):
        manager.rehydrate("s")
    assert manager._events["s"] is None
