"""Idle bridge survival without model calls or automatic replay of player input."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.environment.default.godot.runtime import DockerRuntime, mcp_succeeded
from agentevolver.environment.default.godot.environment import GodotEnvironment


def result(ok=True):
    return SimpleNamespace(isError=False, structuredContent={"ok": ok}, content=[])


@pytest.mark.asyncio
async def test_idle_probe_is_read_only_and_does_not_replay_player_commands(tmp_path):
    runtime = DockerRuntime(tmp_path, "unused", "unused", [])
    runtime.heartbeat_interval = .02
    client = SimpleNamespace(call_tool=AsyncMock(return_value=result()))
    task = asyncio.create_task(runtime._serve_requests(client))
    try:
        await asyncio.sleep(.05)
        client.call_tool.assert_not_awaited()  # No game, no idle traffic.
        future = asyncio.get_running_loop().create_future()
        await runtime.queue.put(("run_project", {"projectPath": "game"}, future))
        await future
        await asyncio.sleep(.07)
        assert runtime.heartbeat_count >= 2
        calls = client.call_tool.await_args_list
        assert calls[0].args[0] == "run_project"
        assert all(c.args == ("game_input_state", {"action": "query"}) for c in calls[1:])
        future = asyncio.get_running_loop().create_future()
        await runtime.queue.put(("stop_project", {}, future))
        await future
        count = client.call_tool.await_count
        await asyncio.sleep(.06)
        assert client.call_tool.await_count == count
    finally:
        await runtime.queue.put(None)
        await task


@pytest.mark.asyncio
async def test_failed_heartbeat_records_failure_without_restart_or_input_retry(tmp_path):
    runtime = DockerRuntime(tmp_path, "unused", "unused", [])
    runtime.heartbeat_interval = .01
    runtime.game_running = True
    client = SimpleNamespace(call_tool=AsyncMock(return_value=result(False)))
    task = asyncio.create_task(runtime._serve_requests(client))
    try:
        await asyncio.sleep(.05)
        assert runtime.bridge_error
        assert runtime.game_running  # Disconnection does not establish process exit.
        client.call_tool.assert_awaited_once_with("game_input_state", {"action": "query"})
    finally:
        await runtime.queue.put(None)
        await task


def test_backend_failure_envelopes_override_successful_mcp_transport():
    assert not mcp_succeeded(result(False))
    r = result()
    r.content = [SimpleNamespace(type="text", text='{"ok":false,"error":{"message":"disconnected"}}')]
    assert not mcp_succeeded(r)


@pytest.mark.asyncio
async def test_stalled_probe_is_bounded_and_exposes_timeout(tmp_path):
    runtime = DockerRuntime(tmp_path, "unused", "unused", [])
    runtime.game_running = True
    runtime.heartbeat_interval = .01
    runtime.heartbeat_timeout = .01
    async def stalled(*args):
        await asyncio.Event().wait()
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=stalled))
    task = asyncio.create_task(runtime._serve_requests(client))
    try:
        await asyncio.sleep(.06)
        assert runtime.bridge_error == "TimeoutError"
        client.call_tool.assert_awaited_once()
    finally:
        await runtime.queue.put(None)
        await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_failed_bridge_never_projects_an_old_screenshot():
    env = GodotEnvironment()
    env._sessions["test"] = {"last": {}, "project": None, "running": True,
                              "screenshots": ["stale-frame"]}
    env._runtimes["test"] = SimpleNamespace(bridge_error="Heartbeat timed out")
    state = await env.get_state(ctx=SimpleNamespace(id="test"))
    assert "Interaction bridge unavailable" in state["state"]
    assert not state["extra"]["screenshots"]
    assert env._sessions["test"]["running"]  # Keep stop_game usable.


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_game_survives_more_than_server_idle_timeout(bound_session):
    root = bound_session["workspace"]
    project = root / "game"
    shutil.copytree(Path(__file__).parent / "fixtures/godot_native", project)
    env = GodotEnvironment()
    ctx = SimpleNamespace(id="idle-bridge-test")

    def require(value):
        assert value["success"], value
        return value

    try:
        require(await env.prepare_workspace(ctx=ctx))
        require(await env.open_project(project_path="game", ctx=ctx))
        require(await env.import_project(ctx=ctx))
        require(await env.start_game(ctx=ctx))
        runtime = env._runtimes[ctx.id]
        native_name = runtime.name
        # No agent operations for longer than the pinned server's 60-second lease.
        await asyncio.sleep(35)
        await asyncio.sleep(35)
        assert runtime.heartbeat_count >= 4 and not runtime.bridge_error
        require(await env.observe(ctx=ctx))
        before = json.loads((project / "observed.json").read_text())
        require(await env.press_key(key="Right", duration_ms=200, ctx=ctx))
        after = json.loads((project / "observed.json").read_text())
        assert after["x"] > before["x"] and not after["held"]
        require(await env.click(x=100, y=110, ctx=ctx))
        assert json.loads((project / "observed.json").read_text())["clicks"] > before["clicks"]
        assert runtime.name == native_name  # Same game/container; no hidden restart.
        require(await env.stop_game(ctx=ctx))
    finally:
        await env.close_session(ctx.id)
