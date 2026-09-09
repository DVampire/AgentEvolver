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
