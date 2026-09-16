"""A name that was written to disk cannot be renamed by renaming the field.

Three formats in this repository spell "what sort of thing is this" inside data that
outlives the process: a saved canvas flow, a trace event's metadata, and the trajectory
projector's resumable state. All three said `kind` before the rename that made every
in-memory field say `type`, and files written then are still on disks.

The rename broke the first one and no test noticed, because none existed. It failed in
the worst available way: `GraphNode` sets `extra="ignore"`, so an unrecognised `kind`
was dropped and every node fell back to its `step` default — inputs and outputs became
steps, and their edges vanished with the ports that no longer existed. A flow loaded,
rendered, and was wrong.

The rule these pin is one sentence: **write the current name, read both.** A reader that
accepts only the new spelling has silently redefined what the old files mean.
"""

from __future__ import annotations

import json
import os

import pytest


# --------------------------------------------------------------------------- #
# A saved canvas flow
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["type", "kind"])
def test_a_saved_node_keeps_the_role_it_was_saved_with(spelling):
    """`extra="ignore"` is what makes this silent, and silence is what makes it bad.

    An unread `kind` is not an error — it is dropped, the field takes its default, and
    an input node becomes a step. Nothing in the file, the load, or the render says so.
    """
    from agentevolver.canvas.types import GraphNode

    node = GraphNode.model_validate({"id": "n1", spelling: "input", "name": "topic"})
    assert node.type == "input", f"a flow saved with {spelling!r} loaded as {node.type!r}"


def test_a_saved_flow_keeps_its_inputs_and_outputs():
    """The whole-file consequence, not just the field.

    A flow whose inputs and outputs turn into steps has lost the ports its edges name,
    so this is what the reader would actually see go missing.
    """
    from agentevolver.canvas.types import FlowGraph

    flow = FlowGraph.model_validate(
        {
            "nodes": [
                {"id": "in", "kind": "input", "name": "topic"},
                {"id": "s1", "kind": "step", "step_type": "agent", "target": "code_agent"},
                {"id": "out", "kind": "output", "name": "result"},
            ],
            "edges": [],
        }
    )
    assert [node.type for node in flow.nodes] == ["input", "step", "output"]


# --------------------------------------------------------------------------- #
# A trace file's compaction marker
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["type", "kind"])
def test_a_recorded_fold_is_still_recognised_as_one(spelling):
    """Unrecognised, a fold leaves the summary *and* the turns it replaced in the
    derived history — so the model reads the same work twice, and the run pays for it
    in the context that compaction existed to reduce."""
    from agentevolver.trace.derive import derive_messages
    from agentevolver.trace.surface import replace_op
    from agentevolver.trace.types import (
        TraceEvent,
        TraceEventType,
        agent_call_event,
        agent_start_event,
    )

    # Built through the factories `trace_manager.emit` uses, so these carry the same
    # surface membership a real log has — a replacement may only cite what is on it.
    events = [
        agent_start_event("s", "t", "a", "go"),
        agent_call_event("s", "t", "a", 1, reasoning="ran the tests"),
    ]
    for position, event in enumerate(events):
        event.seq_no = position
    events.append(
        TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id="s",
            seq_no=2,
            message="Earlier: ran the tests.",
            metadata={spelling: "compaction"},
            surface_op=replace_op(0, 1),
            source_event_seqs=[0, 1],
        )
    )
    texts = [m.text for m in derive_messages(events)]
    assert texts == ["Earlier: ran the tests."], (
        f"a fold recorded with {spelling!r} left {texts} instead of only its summary"
    )


# --------------------------------------------------------------------------- #
# The projector's resumable state
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["type", "kind"])
def test_a_state_file_written_before_the_rename_is_still_resumable(tmp_path, spelling):
    """Loud rather than silent — it raises — but refusing it throws away a projection
    that only needed to be continued, and the next run rebuilds from seq 0."""
    from agentevolver.trajectory.projector import (
        PROJECTION_STATE_VERSION,
        PROJECTOR_VERSION,
        IncrementalTrajectoryProjector,
    )

    projector = IncrementalTrajectoryProjector(trace_reader=None, trace_root=str(tmp_path))
    path = projector._state_path("s1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    spelling: "trajectory_projection_state",
                    "schema_version": PROJECTION_STATE_VERSION,
                    "projector_version": PROJECTOR_VERSION,
                    "session_id": "s1",
                }
            )
            + "\n"
        )

    state = projector._load_state("s1")
    assert state is not None, f"a state file written with {spelling!r} was refused"
