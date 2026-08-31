"""Incremental Trace reads and projection cursors remain resumable across processes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentevolver.trace.projection import (
    ProjectionVersionMismatch,
    ProjectionWatermarkError,
    ProjectionWatermarkStore,
)
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.trace.writer import TraceWriter
from agentevolver.utils import AsyncQueue


def _writer(tmp_path):
    return TraceWriter(str(tmp_path / "trace"), AsyncQueue())


def _write_jsonl(writer, session_id, payloads):
    path = writer._session_path(session_id)
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(
        "".join(json.dumps(payload, default=str) + "\n" for payload in payloads),
        encoding="utf-8",
    )


def test_read_from_filters_while_streaming_and_honours_the_batch_limit(tmp_path):
    writer = _writer(tmp_path)
    payloads = [
        TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="s", seq_no=index, label=f"event-{index}"
        ).to_dict()
        for index in range(6)
    ]
    _write_jsonl(writer, "s", payloads)

    batch = writer.read_from("s", after_seq=2, limit=2)

    assert [event["seq_no"] for event in batch] == [3, 4]
    assert writer.read_from("s", after_seq=5) == []


def test_read_from_assigns_line_sequences_to_pre_sequence_logs(tmp_path):
    writer = _writer(tmp_path)
    payloads = [
        TraceEvent(
            event_type=TraceEventType.CUSTOM, session_id="legacy", label=f"event-{index}"
        ).to_dict()
        for index in range(3)
    ]
    for payload in payloads:
        payload.pop("seq_no", None)
    _write_jsonl(writer, "legacy", payloads)

    assert [event["seq_no"] for event in writer.read_from("legacy", after_seq=0)] == [1, 2]


def test_projection_watermark_resumes_monotonically_and_survives_reload(tmp_path):
    first_process = ProjectionWatermarkStore(str(tmp_path / "trace"))
    committed = first_process.advance("trajectory", 3, "session/one", 41)

    second_process = ProjectionWatermarkStore(str(tmp_path / "trace"))

    assert committed.last_seq == 41
    assert second_process.after_seq("trajectory", 3, "session/one") == 41
    assert second_process.advance("trajectory", 3, "session/one", 44).last_seq == 44
    with pytest.raises(ProjectionWatermarkError, match="regression"):
        second_process.advance("trajectory", 3, "session/one", 43)


def test_projection_version_change_requires_an_explicit_rebuild(tmp_path):
    store = ProjectionWatermarkStore(str(tmp_path / "trace"))
    store.advance("trajectory", 3, "session", 10)

    with pytest.raises(ProjectionVersionMismatch, match="rebuild"):
        store.after_seq("trajectory", 4, "session")


def test_corrupt_projection_watermark_is_never_treated_as_no_progress(tmp_path):
    store = ProjectionWatermarkStore(str(tmp_path / "trace"))
    path = store.path("trajectory", "session")
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ProjectionWatermarkError, match="cannot read"):
        store.after_seq("trajectory", 1, "session")
