"""Adoption validates and persists decisions without adding an Agent exit gate."""

import json
from types import SimpleNamespace

import pytest

from agentevolver.extension import extension_manager
from agentevolver.extension.types import Manifest, ManifestComponent
from agentevolver.tool.default.adoption import AdoptionTool


@pytest.fixture
def extensions(tmp_path, monkeypatch):
    active = {}
    monkeypatch.setattr(extension_manager, "base_dir", str(tmp_path))
    monkeypatch.setattr(type(extension_manager), "read_manifest", lambda self: Manifest(components=[
        ManifestComponent(module="skill", name=name, version=version, file=f"skill/{name}")
        for name, version in active.items()
    ]))
    monkeypatch.setattr(type(extension_manager), "list_component_versions", lambda *a: ["1.0", "2.0"])
    return active


def evaluation(version="1.0", verdict="pass"):
    return {
        "module": "skill", "name": "checker", "version": version, "verdict": verdict,
        "baseline": "Missing input validation", "cases": [{
            "case_id": "invalid-input", "expected": "Reject invalid input",
            "observed": "Rejected invalid input", "passed": verdict == "pass",
            "evidence_ids": ["test-call-1"],
        }],
    }


@pytest.fixture
def caller(monkeypatch):
    from agentevolver.message.types import ToolMessage
    from agentevolver.runtime import kernel

    ctx = SimpleNamespace(id="unit-evaluation", extra={"process_pid": "unit-evaluation"})
    proc = SimpleNamespace(agent=SimpleNamespace(conversation=SimpleNamespace(items=[
        ToolMessage(content="Rejected invalid input", tool_call_id="test-call-1"),
    ])))
    monkeypatch.setattr(kernel, "get", lambda pid: proc if pid == ctx.id else None)
    return ctx


@pytest.mark.asyncio
async def test_keep_is_validated_and_persisted_by_the_adoption_tool(extensions, caller, tmp_path):
    extensions["checker"] = "1.0"
    result = await AdoptionTool()(action="record_decision", report=evaluation(),
                                  decision="keep", evidence="Observed test-call-1", ctx=caller)
    assert result.success, result.message
    records = json.loads((tmp_path / ".evaluations.json").read_text())
    assert records[0]["decision"] == "keep"
    assert records[0]["evaluation"]["version"] == "1.0"
    assert caller.extra == {"process_pid": "unit-evaluation"}


@pytest.mark.asyncio
@pytest.mark.parametrize("version,verdict", [("2.0", "pass"), ("1.0", "fail")])
async def test_keep_rejects_wrong_version_or_failed_evaluation(extensions, caller, tmp_path, version, verdict):
    extensions["checker"] = "1.0"
    result = await AdoptionTool()(action="record_decision", report=evaluation(version, verdict),
                                  decision="keep", evidence="Observed test-call-1", ctx=caller)
    assert not result.success
    assert not (tmp_path / ".evaluations.json").exists()


@pytest.mark.asyncio
async def test_a_rollback_decision_does_not_claim_to_have_unloaded_the_candidate(extensions, caller, tmp_path):
    extensions["checker"] = "1.0"
    result = await AdoptionTool()(action="record_decision", report=evaluation(verdict="fail"),
                                  decision="rollback", evidence="Needs actual rollback", ctx=caller)
    assert result.success, result.message
    assert extensions["checker"] == "1.0"
    records = json.loads((tmp_path / ".evaluations.json").read_text())
    assert records[-1]["decision"] == "rollback"


@pytest.mark.asyncio
async def test_corrupt_ledger_cannot_accept_a_new_keep(extensions, caller, tmp_path):
    extensions["checker"] = "1.0"
    (tmp_path / ".evaluations.json").write_text("{broken")
    result = await AdoptionTool()(action="record_decision", report=evaluation(),
                                  decision="keep", evidence="Observed test-call-1", ctx=caller)
    assert not result.success
    assert (tmp_path / ".evaluations.json").read_text() == "{broken"
