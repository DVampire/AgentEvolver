"""Call lookup must not guess step values, accept start events, or hide shell failures."""
import json
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).parents[1]
receipts = runpy.run_path(str(ROOT / "agentevolver/skill/evolving/self_evolving_skill/scripts/evidence.py"))["receipts"]


def event(call_id, *, step=None, kind="tool_call", name="bash_tool", success=True, message="observed"):
    return {"event_type": kind, "step_number": step, "action_name": name,
            "success": success, "message": message, "metadata": {"call_id": call_id}}


def test_lookup_handles_null_steps_live_tail_and_consumer_chronology(tmp_path):
    path = tmp_path / "trace.jsonl"
    items = [event("baseline"), event("consumer", kind="tool_start"),
             event("consumer", step=3, kind="skill_call", name="factor_lab__evaluate"),
             event("check", step=3), event("failed", step=4, success=False)]
    path.write_text("\n".join(json.dumps(e) for e in items) + '\n{"unfinished":')
    assert [r["call_id"] for r in receipts(path, after_call="consumer")] == ["consumer", "check", "failed"]
    assert [r["call_id"] for r in receipts(path, limit=2)] == ["check", "failed"]
    assert receipts(path)[0]["step_number"] is None
    assert len(receipts(path, name="factor_lab__evaluate")) == 1
    with pytest.raises(ValueError, match="No completed call"):
        receipts(path, after_call="invented")


def test_shell_observation_separates_transport_success_and_exit_code(tmp_path):
    path = tmp_path / "trace.jsonl"
    failed = "STDERR:\ncompile failed\n\nExit code: 1\n\n[📄 full output archived at /session/log/bash/failure.txt]"
    quoted = "STDOUT:\nprevious log said Exit code: 9\nbut this is text\n\n[📄 full output archived at /session/log/bash/read.txt]"
    path.write_text("\n".join(json.dumps(event(c, message=m)) for c, m in (("failed", failed), ("quoted", quoted))))
    rows = receipts(path)
    assert rows[0]["success"] is True and rows[0]["shell_exit_code"] == 1
    assert rows[0]["archive_path"].endswith("failure.txt")
    assert "compile failed" in rows[0]["diagnostic"]
    assert "shell_exit_code" not in rows[1]


def test_invalid_complete_line_is_not_silently_skipped(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"bad":\n')
    with pytest.raises(ValueError, match="line 1"):
        receipts(path)
