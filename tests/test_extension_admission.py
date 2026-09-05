"""Deterministic admission protects the extension registry transaction."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.parametrize("module", ["tool", "agent", "skill", "connector", "plugin", "workflow", "environment", "memory"])
def test_generic_adoption_requires_versioned_passing_evidence(tmp_path, monkeypatch, module):
    from agentevolver.extension.types import Manifest, ManifestComponent

    manager = ExtensionManagerServer(base_dir=str(tmp_path))
    manifest = Manifest(components=[ManifestComponent(module=module, name="candidate", version="2", file="unused")])
    monkeypatch.setattr(type(manager), "read_manifest", lambda self: manifest)
    monkeypatch.setattr(type(manager), "list_component_versions", lambda *args: ["1", "2"])
    report = {"module": module, "name": "candidate", "version": "2", "verdict": "pass",
              "baseline": "Previously failed this case", "cases": [
                  {"case_id": "regression", "expected": "valid result", "observed": "valid result",
                   "passed": True, "evidence_ids": ["real-call"]}]}
    record = manager.record_decision(report=report, run_id="eval-run", decision="keep", evidence="Recurring defect")
    assert record["evaluation"]["module"] == module
    assert (tmp_path / ".evaluations.json").exists()
    with pytest.raises(ValueError, match="inactive candidate"):
        manager.record_decision(report={**report, "version": "1"}, run_id="eval-run", decision="keep", evidence="x")
    with pytest.raises(ValueError, match="passing"):
        manager.record_decision(report={**report, "verdict": "fail"}, run_id="eval-run", decision="keep", evidence="x")


@pytest.mark.asyncio
async def test_evaluator_rejects_invented_observation_ids():
    import json
    from types import SimpleNamespace
    from agentevolver.agent.actor.evaluate_agent import EvaluateAgent
    from agentevolver.response.types import Response, ResponseType

    agent = EvaluateAgent()
    agent.ctx = SimpleNamespace(extra={"target_type": "skill", "target_name": "candidate", "evaluation_version": "2"})
    report = {"module": "skill", "name": "candidate", "version": "2", "verdict": "pass",
              "baseline": "previous result", "cases": [
                  {"case_id": "case", "expected": "x", "observed": "x", "passed": True,
                   "evidence_ids": ["invented"]}]}
    response = await agent.finalize(Response(type=ResponseType.AGENT, success=True, message=json.dumps(report)))
    assert "evaluation" not in response.data
    assert "absent" in response.data["evaluation_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("active_version", ["2", "3", None])
async def test_evaluator_binds_real_evidence_to_unchanged_version(monkeypatch, active_version):
    import json
    from types import SimpleNamespace
    from agentevolver.agent.actor.evaluate_agent import EvaluateAgent
    from agentevolver.extension import extension_manager
    from agentevolver.message.types import ToolMessage
    from agentevolver.response.types import Response, ResponseType

    active = Manifest(components=[] if active_version is None else [
        ManifestComponent(module="skill", name="candidate", version=active_version, file="unused"),
    ])
    monkeypatch.setattr(type(extension_manager), "read_manifest", lambda self: active)
    agent = EvaluateAgent()
    agent.ctx = SimpleNamespace(extra={"target_type": "skill", "target_name": "candidate", "evaluation_version": "2"})
    agent.conversation.items.append(ToolMessage(tool_call_id="observed", content="actual output"))
    report = {"module": "skill", "name": "candidate", "version": "2", "verdict": "pass",
              "baseline": "previous result", "cases": [
                  {"case_id": "case", "expected": "x", "observed": "x", "passed": True,
                   "evidence_ids": ["observed"]}]}
    response = await agent.finalize(Response(type=ResponseType.AGENT, success=True,
        message=json.dumps(report), data={"evaluation": {"unvalidated": True}}))
    if active_version == "2":
        assert response.data["evaluation"] == report
    else:
        assert "evaluation" not in response.data
        assert "changed" in response.data["evaluation_error"]

from agentevolver.extension.server import ExtensionManagerServer
from agentevolver.extension.types import Manifest, ManifestComponent


@pytest.mark.asyncio
@pytest.mark.parametrize("parent, exited, status, valid", [
    ("caller", True, "done", True), ("another", True, "done", False),
    ("caller", False, "done", False), ("caller", True, "failed", False),
])
async def test_adoption_requires_own_completed_evaluator(monkeypatch, parent, exited, status, valid):
    from types import SimpleNamespace
    from agentevolver.agent.actor.evaluate_agent import EvaluateAgent
    from agentevolver.extension import extension_manager
    from agentevolver.runtime import kernel
    from agentevolver.tool.default.adoption import AdoptionTool

    report = {"module": "skill", "name": "candidate", "version": "2"}
    proc = SimpleNamespace(agent=EvaluateAgent(), parent_pid=parent,
        _exited=SimpleNamespace(is_set=lambda: exited), exit_status=SimpleNamespace(value=status),
        last_result=SimpleNamespace(data={"evaluation": report}))
    monkeypatch.setattr(kernel, "get", lambda pid: proc)
    calls = []
    def record(self, **kwargs):
        calls.append(kwargs)
        return kwargs
    monkeypatch.setattr(type(extension_manager), "record_decision", record)
    result = await AdoptionTool()(action="record_decision", run_id="evaluation-run",
        decision="keep", evidence="repeated regression", ctx=SimpleNamespace(extra={"process_pid": "caller"}))
    assert result.success is valid
    assert bool(calls) is valid


class _Entry:
    manager_instance = None

    @classmethod
    def manager(cls):
        return cls.manager_instance


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["tool", "agent", "skill", "connector", "plugin", "workflow", "environment", "memory"])
async def test_all_families_must_pass_isolation_before_live_load(tmp_path, monkeypatch, module):
    manager = ExtensionManagerServer(base_dir=str(tmp_path))
    checked = []

    async def denied(kind, path, config):
        checked.append(kind)
        raise RuntimeError("isolation unavailable")

    monkeypatch.setattr(manager, "check", denied)
    with pytest.raises(RuntimeError, match="isolation unavailable"):
        await manager._load_component(module, "untrusted", None, version=None, config=None)
    assert checked == [module]
    assert not manager.read_manifest().components


@pytest.mark.asyncio
async def test_real_isolated_admission_loads_tool_without_touching_live_registry(tmp_path):
    from pathlib import Path
    from agentevolver.tool import tool_manager

    manager = ExtensionManagerServer(base_dir=str(tmp_path))
    source = Path(__file__).resolve().parents[1] / "agentevolver/tool/default/coordination/reply.py"
    before = await tool_manager.get_info("reply_tool")
    admitted = Path(await manager.check("tool", str(source)))
    assert admitted.read_bytes() == source.read_bytes()
    assert (admitted.parent / "result.json").exists()
    assert await tool_manager.get_info("reply_tool") == before


@pytest.mark.asyncio
async def test_isolated_candidate_cannot_mutate_host_before_rejection(tmp_path):
    manager = ExtensionManagerServer(base_dir=str(tmp_path / "extension"))
    sentinel = tmp_path / "must-not-exist"
    source = tmp_path / "unsafe.py"
    source.write_text(f"open({str(sentinel)!r}, 'w').write('side effect')\n")
    with pytest.raises(ValueError, match="Isolated admission failed"):
        await manager.add_component("tool", str(source))
    assert not sentinel.exists()
    assert not manager.read_manifest().components


class _Manager:
    def __init__(self, info=object(), schemas=None):
        self.info = info
        self.schemas = schemas or []

    async def get_info(self, _name):
        return self.info

    async def function_callings(self, _allowlist):
        return self.schemas


def _function(parameters=None):
    return ({
        "type": "function",
        "function": {
            "name": "candidate_tool",
            "description": "Candidate",
            "parameters": parameters or {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }, ("tool", "candidate_tool"))


@pytest.mark.asyncio
async def test_admission_accepts_a_registered_json_serializable_contract(tmp_path):
    _Entry.manager_instance = _Manager(schemas=[_function()])
    manager = ExtensionManagerServer(base_dir=str(tmp_path))

    with patch("agentevolver.capability.types.stored_type", return_value=_Entry()):
        await manager._validate_candidate("tool", "candidate_tool")


@pytest.mark.asyncio
async def test_admission_rejects_a_candidate_missing_from_its_manager(tmp_path):
    _Entry.manager_instance = _Manager(info=None)
    manager = ExtensionManagerServer(base_dir=str(tmp_path))

    with patch("agentevolver.capability.types.stored_type", return_value=_Entry()):
        with pytest.raises(LookupError, match="not registered after load"):
            await manager._validate_candidate("tool", "candidate_tool")


@pytest.mark.asyncio
async def test_admission_rejects_an_invalid_parameter_contract(tmp_path):
    invalid = {
        "type": "object",
        "properties": {},
        "required": ["missing"],
        "additionalProperties": False,
    }
    _Entry.manager_instance = _Manager(schemas=[_function(invalid)])
    manager = ExtensionManagerServer(base_dir=str(tmp_path))

    with patch("agentevolver.capability.types.stored_type", return_value=_Entry()):
        with pytest.raises(ValueError, match="required"):
            await manager._validate_candidate("tool", "candidate_tool")


@pytest.mark.asyncio
async def test_admission_rejects_a_non_json_schema(tmp_path):
    invalid = {
        "type": "object",
        "properties": {"value": {"default": object()}},
        "additionalProperties": False,
    }
    _Entry.manager_instance = _Manager(schemas=[_function(invalid)])
    manager = ExtensionManagerServer(base_dir=str(tmp_path))

    with patch("agentevolver.capability.types.stored_type", return_value=_Entry()):
        with pytest.raises(TypeError, match="JSON serializable"):
            await manager._validate_candidate("tool", "candidate_tool")


@pytest.mark.asyncio
async def test_a_rejected_new_candidate_is_unloaded_before_manifest_commit(
    tmp_path, monkeypatch,
):
    manager = ExtensionManagerServer(base_dir=str(tmp_path))
    unloaded = []

    async def load(*_args, **_kwargs):
        return "candidate_tool", "1.0.0"

    async def reject(*_args, **_kwargs):
        raise ValueError("bad contract")

    async def unload(module, name):
        unloaded.append((module, name))
        return True

    monkeypatch.setattr(manager, "_load_component", load)
    monkeypatch.setattr(manager, "_validate_candidate", reject)
    monkeypatch.setattr(manager, "_unload_component", unload)

    with pytest.raises(ValueError, match="deterministic admission"):
        await manager.add_component("tool", str(tmp_path / "candidate_tool.py"))

    assert unloaded == [("tool", "candidate_tool")]
    assert manager.read_manifest().components == []


@pytest.mark.asyncio
async def test_a_rejected_evolution_restores_the_previous_version(
    tmp_path, monkeypatch,
):
    manager = ExtensionManagerServer(base_dir=str(tmp_path))
    manager._write_manifest(Manifest(components=[ManifestComponent(
        module="tool",
        name="candidate_tool",
        version="1.0.0",
        file="tool/candidate_tool.py",
    )]))
    restored = []

    async def load(*_args, **_kwargs):
        return "candidate_tool", "1.1.0"

    async def reject(*_args, **_kwargs):
        raise ValueError("bad contract")

    async def rollback(module, name, version, config=None):
        restored.append((module, name, version))
        return name

    monkeypatch.setattr(manager, "_load_component", load)
    monkeypatch.setattr(manager, "_validate_candidate", reject)
    monkeypatch.setattr(manager, "rollback", rollback)

    with pytest.raises(ValueError, match="deterministic admission"):
        await manager.add_component("tool", str(tmp_path / "candidate_tool.py"))

    assert restored == [("tool", "candidate_tool", "1.0.0")]
    assert manager.read_manifest().find("tool", "candidate_tool").version == "1.0.0"
