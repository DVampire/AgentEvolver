"""Shared launchers must register runs even when an old caller passes no-monitor."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.visual.run.server import RunMonitor


@pytest.mark.asyncio
@pytest.mark.parametrize("no_monitor", [False, True])
async def test_shared_launcher_always_registers_run(monkeypatch, tmp_path, no_monitor):
    from examples import run_meta_agent as launcher

    monkeypatch.setattr(launcher, "config", SimpleNamespace(
        log_root=str(tmp_path / "log"), extension_root=str(tmp_path / "extension")))
    start = AsyncMock(return_value="http://127.0.0.1:9876/s/run-test/")
    monkeypatch.setattr(RunMonitor, "start", start)
    args = SimpleNamespace(no_monitor=no_monitor, agent_name="game_builder_agent", monitor_port=8766)
    monitor = await launcher.start_run_record(args, "test-session")
    start.assert_awaited_once_with(8766)
    state = json.loads(monitor.path.read_text())
    assert state["session_id"] == "test-session"
    assert state["status"] == "running"
    assert state["title"] == "Game Builder Agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(RuntimeError("registration unavailable"), "failed"),
                                          (asyncio.CancelledError(), "interrupted")])
async def test_registration_failure_cannot_be_silently_ignored(monkeypatch, tmp_path, error, status):
    from examples import run_meta_agent as launcher

    monkeypatch.setattr(launcher, "config", SimpleNamespace(
        log_root=str(tmp_path / "log"), extension_root=str(tmp_path / "extension")))
    monkeypatch.setattr(RunMonitor, "start", AsyncMock(side_effect=error))
    args = SimpleNamespace(no_monitor=True, agent_name="game_builder_agent", monitor_port=8766)
    with pytest.raises(type(error)):
        await launcher.start_run_record(args, "test-session")
    assert json.loads((tmp_path / "log/run_monitor.json").read_text())["status"] == status


@pytest.mark.asyncio
async def test_new_experiments_do_not_load_a_previous_capability_library(monkeypatch, tmp_path):
    from examples import run_meta_agent as launcher
    from agentevolver.paths import P, path_manager
    from agentevolver.sandbox.project import ProjectSandbox

    shared = tmp_path / "libraries"
    shared.mkdir()
    (shared / "manifest.json").write_text('{"components":[{"name":"old_candidate"}]}')
    cfg = SimpleNamespace(values=lambda: [])

    def initialize(**kwargs):
        cfg.extension_root = str(shared)
        cfg.workspace_root = str(tmp_path / "startup/workspace")
        cfg.log_root = str(tmp_path / "startup/log")
        cfg.log_path = "agent.log"

    cfg.initialize = initialize
    monkeypatch.setattr(launcher, "config", cfg)
    monkeypatch.setattr(launcher, "parse_args", lambda: SimpleNamespace(config="unused", plugins=None))
    ids = iter(("experiment_a", "experiment_b"))
    monkeypatch.setattr(launcher, "make_id", lambda: next(ids))
    sandboxes = []

    def sandbox(ctx, *, shared_extension_root):
        value = ProjectSandbox.create(tmp_path / "sessions" / ctx.id,
                                      shared_extension_root=shared_extension_root)
        sandboxes.append(value)
        return value

    class StopBeforeModels(Exception):
        pass

    observed = []

    def before_managers(root):
        observed.append((root, path_manager.get(P.EXTENSION)))
        raise StopBeforeModels

    monkeypatch.setenv("AGENTEVOLVER_EXTENSION_ROOT", str(shared))
    monkeypatch.setattr(launcher, "ensure_session_sandbox", sandbox)
    monkeypatch.setattr(launcher.extension_manager, "set_base_dir", before_managers)
    for _ in range(2):
        with pytest.raises(StopBeforeModels):
            await launcher.main()
    assert observed[0][0] != observed[1][0]
    for (root, managed_root), value in zip(observed, sandboxes):
        assert managed_root == value.shared_extension_root
        assert root == str(managed_root)
        assert managed_root.parent == shared
        assert not (managed_root / "manifest.json").exists()
        assert value.extension_root != managed_root  # staging and adopted capabilities remain separate
    assert sandboxes[0].workspace_root != sandboxes[1].workspace_root
    assert sandboxes[0].plan_root != sandboxes[1].plan_root
