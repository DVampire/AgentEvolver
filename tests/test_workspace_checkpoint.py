"""Workspace checkpoints restore overwritten files and remove newly created ones.

Mutating tools capture recovery data before approval and execution; an incorrect snapshot
would either discard the original bytes or leave a file that did not exist. The fixture
also restores global path bindings so checkpoint tests cannot contaminate later cases.
"""

from types import SimpleNamespace

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.permission import Operation, PermissionRequest
from agentevolver.tool.checkpoint import (
    capture_file_checkpoint,
    restore_file_checkpoint,
)
from agentevolver.tool.execution import ToolExecution


@pytest.fixture(autouse=True)
def restore_path_binding():
    owner, session = path_manager._owner, path_manager._session_id
    overrides = dict(path_manager._overrides)
    yield
    path_manager._owner, path_manager._session_id = owner, session
    path_manager._overrides = overrides


def _execution():
    return ToolExecution.create(
        name="write_file_tool",
        version="1",
        arguments={"path": "x"},
        ctx=SimpleNamespace(id="s", name="meta_agent", extra={}),
    )


def _bind_log(log):
    path_manager.bind_session("checkpoint-test", "session")
    path_manager.override(P.SESSION_LOG, log)


def test_checkpoint_restores_overwritten_file(tmp_path):
    target = tmp_path / "source.py"
    target.write_text("before\n", encoding="utf-8")
    log = tmp_path / "log"
    _bind_log(log)
    info = capture_file_checkpoint(
        _execution(),
        PermissionRequest(op=Operation.WRITE, target=str(target), content="after"),
    )
    target.write_text("after\n", encoding="utf-8")

    assert restore_file_checkpoint(info["path"]) == str(target)
    assert target.read_text(encoding="utf-8") == "before\n"


def test_checkpoint_restores_new_file_by_removing_it(tmp_path):
    target = tmp_path / "new.py"
    _bind_log(tmp_path / "log")
    info = capture_file_checkpoint(
        _execution(),
        PermissionRequest(op=Operation.WRITE, target=str(target), content="new"),
    )
    target.write_text("new", encoding="utf-8")

    restore_file_checkpoint(info["path"])
    assert not target.exists()
