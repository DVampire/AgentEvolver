"""Deterministic admission protects the extension registry transaction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentevolver.extension.server import ExtensionManagerServer
from agentevolver.extension.types import Manifest, ManifestComponent


class _Entry:
    manager_instance = None

    @classmethod
    def manager(cls):
        return cls.manager_instance


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
