"""Real Docker/MCP/Godot integration; explicitly select with -m integration."""
import asyncio
import base64
import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageStat

from agentevolver.environment.default.godot.environment import GodotEnvironment
from agentevolver.environment.default.godot.runtime import docker_command
from agentevolver.tool.default.workspace.bash import BashTool
from agentevolver.tool.default.workspace.container import container_for


def require(result):
    assert result["success"], result
    return result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_native_game_through_shared_base_and_godot(bound_session):
    root = bound_session["workspace"]
    ctx = SimpleNamespace(id="native-integration")
    env = GodotEnvironment()
    bash = BashTool()
    runtime = None
    evidence = {}
    try:
        require(await env.prepare_workspace(ctx=ctx))
        runtime = env._runtimes[ctx.id]
        assert container_for(str(root))[0] == runtime.base_name
        fixture = Path(__file__).parent / "fixtures" / "godot_native"
        sources = {path.name: path.read_text() for path in fixture.iterdir() if path.is_file()}
        plan = bound_session["plan"] / "plan.md"
        program = (
            "import json,pathlib,base64; "
            f"root=pathlib.Path({str(root / 'game')!r}); root.mkdir(); "
            f"files=json.loads(base64.b64decode({base64.b64encode(json.dumps(sources).encode()).decode()!r})); "
            "[(root/name).write_text(content) for name,content in files.items()]; "
            f"pathlib.Path({str(plan)!r}).write_text('# Native environment verification\\n'); "
            "print((root/'project.godot').read_text())"
        )
        written = await bash(command="python -c " + shlex.quote(program), ctx=ctx)
        assert written.success and written.data["exit_code"] == 0, written
        assert "Native environment" in plan.read_text()
        require(await env.doctor(ctx=ctx))
        evidence["version"] = env._sessions[ctx.id]["last"]["output"]
        # Read the exact file through the OTHER container, not just from the host.
        shared = await docker_command("exec", runtime.name, "cat", str(root / "game/project.godot"))
        assert shared == sources["project.godot"]
        assert not (await env.open_project(project_path="../outside", ctx=ctx))["success"]
        require(await env.open_project(project_path="game", ctx=ctx))
        require(await env.import_project(ctx=ctx))
        require(await env.check_script(script="main.gd", ctx=ctx))
        started = require(await env.start_game(ctx=ctx))
        assert started["extra"]["screenshots"]
        before = Image.open(started["extra"]["screenshots"][0].screenshot_path).convert("RGB")
        assert min(before.size) >= 400
        assert sum(ImageStat.Stat(before).var) > 100, "Blank or uniform rendered frame"
        tree = require(await env.inspect_runtime(kind="tree", ctx=ctx))
        assert "CharacterBody3D" in tree["message"] and "Player" in tree["message"]
        moved = require(await env.press_key(key="Right", duration_ms=650, ctx=ctx))
        await asyncio.sleep(0.15)
        state = json.loads((root / "game/observed.json").read_text())
        assert state["x"] > 0.3 and state["held"] is False, state
        evidence["after_movement"] = state
        after = Image.open(moved["extra"]["screenshots"][0].screenshot_path).convert("RGB")
        assert ImageChops.difference(before, after).getbbox(), "Input did not change pixels"
        # The orange companion itself must move in the rendered scene.
        def orange_center(image):
            pixels = image.load()
            points = [x for y in range(image.height) for x in range(image.width)
                      if pixels[x, y][0] > 180 and 40 < pixels[x, y][1] < 170 and pixels[x, y][2] < 60]
            assert len(points) > 100
            return sum(points) / len(points)
        assert orange_center(after) > orange_center(before) + 10
        await asyncio.sleep(0.2)
        released = json.loads((root / "game/observed.json").read_text())
        assert abs(released["x"] - state["x"]) < 0.05, "Key remained held"
        require(await env.move_mouse(x=140, y=115, ctx=ctx))
        clicked = require(await env.click(x=140, y=115, ctx=ctx))
        await asyncio.sleep(0.15)
        state = json.loads((root / "game/observed.json").read_text())
        assert state["clicks"] == 1, state
        evidence["after_click"] = state
        state_view = await env.get_state(ctx=ctx)
        assert state_view["extra"]["screenshots"]
        from agentevolver.agent.actor.game_builder_agent import GameBuilderAgent
        from agentevolver.message.types import ContentPartImage

        agent = GameBuilderAgent()
        agent._environment_observations = {"godot_environment": state_view}
        assert any(isinstance(part, ContentPartImage) for msg in agent.attachments() for part in msg.content)
        require(await env.stop_game(ctx=ctx))
        assert not (root / "game/override.cfg").exists(), "Transient bridge was left behind"
        assert (root / "game/project.godot").read_text() == sources["project.godot"]
        require(await env.start_game(ctx=ctx))
        require(await env.stop_game(ctx=ctx))
        exported = require(await env.export_project(
            preset="Linux", output_path="builds/r001/game.x86_64", ctx=ctx))
        artifact = Path(exported["extra"]["output_path"])
        assert artifact.stat().st_size > 1_000_000
        native_output = await docker_command(
            "exec", runtime.name, "timeout", "20", str(artifact), "--headless", "--quit-after", "10")
        assert "FIXTURE_READY" in native_output and "ERROR:" not in native_output, native_output
        evidence["export"] = {"bytes": artifact.stat().st_size, "smoke": "FIXTURE_READY"}
        # Broken source must fail rather than looking like a successful launch.
        broken = await bash(command="python -c " + shlex.quote(
            f"from pathlib import Path; Path({str(root / 'game/main.gd')!r}).write_text('this is invalid GDScript')"), ctx=ctx)
        assert broken.success and broken.data["exit_code"] == 0
        assert not (await env.check_script(script="main.gd", ctx=ctx))["success"]
        assert not (await env.start_game(ctx=ctx))["success"]
        evidence["checks"] = ["shared file IO", "plan IO", "import", "parse", "3D render", "held input",
                              "key release", "mouse movement", "click", "model image routing", "restart",
                              "native export and executable smoke", "bad script rejection"]
        target = Path(os.environ.get("GODOT_TEST_ARTIFACTS", str(root / "reports")))
        target.mkdir(parents=True, exist_ok=True)
        before.save(target / "before.png")
        after.save(target / "after-movement.png")
        Image.open(clicked["extra"]["screenshots"][0].screenshot_path).save(target / "after-click.png")
    finally:
        await env.close_session(ctx.id)
    assert runtime is not None
    assert container_for(str(root)) is None
    for name in (runtime.name, runtime.base_name):
        with pytest.raises(RuntimeError):
            await docker_command("inspect", name)
    evidence["cleanup"] = "both owned containers removed"
    (target / "result.json").write_text(json.dumps(evidence, indent=2))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancelled_input_and_transport_timeout_cleanup(bound_session):
    root = bound_session["workspace"]
    ctx = SimpleNamespace(id="native-cancellation")
    env = GodotEnvironment()
    fixture = Path(__file__).parent / "fixtures" / "godot_native"
    project = root / "game"
    project.mkdir()
    for path in fixture.iterdir():
        (project / path.name).write_bytes(path.read_bytes())
    try:
        require(await env.doctor(ctx=ctx))
        require(await env.open_project(project_path="game", ctx=ctx))
        require(await env.import_project(ctx=ctx))
        require(await env.start_game(ctx=ctx))
        runtime = env._runtimes[ctx.id]
        held = asyncio.create_task(env.press_key(key="Right", duration_ms=2000, ctx=ctx))
        await asyncio.sleep(0.25)
        held.cancel()
        with pytest.raises(asyncio.CancelledError):
            await held
        with pytest.raises(RuntimeError):
            await docker_command("inspect", runtime.name)
        # CLI timeout must also terminate the actual engine, not only docker exec.
        assert env._sessions[ctx.id]["running"] is False
        (project / "loop.gd").write_text("extends SceneTree\nfunc _initialize():\n\twhile true:\n\t\tpass\n")
        timed_out = await env.run_headless(script="loop.gd", frames=2, timeout=1, ctx=ctx)
        assert not timed_out["success"] and timed_out["extra"]["timed_out"], timed_out
        with pytest.raises(RuntimeError):
            await docker_command("inspect", runtime.name)
        # File authoring must remain routed to base Docker after an engine failure.
        assert container_for(str(root))[0] == runtime.base_name
        require(await env.start_game(ctx=ctx))
        # Interrupt an actual MCP request, not a mocked sleep; the engine must go away.
        with pytest.raises(asyncio.TimeoutError):
            await runtime.call("game_screenshot", {}, timeout=0.000001)
        with pytest.raises(RuntimeError):
            await docker_command("inspect", runtime.name)
    finally:
        await env.close_session(ctx.id)
    assert not (project / "override.cfg").exists()
    assert container_for(str(root)) is None


@pytest.mark.asyncio
async def test_project_symlink_cannot_escape_workspace(bound_session, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "project.godot").write_text("config_version=5")
    (bound_session["workspace"] / "escape").symlink_to(outside, target_is_directory=True)
    env = GodotEnvironment()
    result = await env.open_project(project_path="escape", ctx=SimpleNamespace(id="path-check"))
    assert not result["success"] and "inside" in result["message"]
    assert not env._runtimes
