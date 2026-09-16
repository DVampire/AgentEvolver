"""Native game publishing uses the existing deployment lifecycle and source archive."""
import importlib.util
from pathlib import Path

import pytest

from agentevolver.deploy.server import DeploymentManagerServer
from agentevolver.deploy.types import DeployRequest
from agentevolver.sandbox.default.docker import DockerSandbox
from agentevolver.sandbox.types import SandboxConfig


def test_godot_profile_defaults_to_owned_docker_without_affecting_web(monkeypatch):
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    manager = DeploymentManagerServer()
    request = DeployRequest(site_id="game", runtime="godot", source_dir="/source")
    assert manager._backend_kind(request) == "docker"
    assert manager._backend_kind(DeployRequest(site_id="web", source_dir="/source")) == "host"
    spec = manager._resolve_spec(request)
    assert spec.image == "agentevolver/godot-play:4.7-v1"
    assert spec.start == "exec /opt/godot-play/start.sh"
    assert spec.port == 6080
    config = SandboxConfig(image=spec.image, network=True,
                           publish_ports={6080: 0}, publish_host="127.0.0.1")
    args = DockerSandbox(config)._run_args()
    assert args[args.index("-p") + 1] == "127.0.0.1::6080"
    assert "-v" not in args  # no authoring workspace or saves mounted


def test_prepare_removes_only_generated_agent_autoload(tmp_path):
    path = Path(__file__).parents[1] / "docker/godot-play/prepare.py"
    spec = importlib.util.spec_from_file_location("godot_preview_prepare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="project.godot"):
        module.prepare(tmp_path)
    (tmp_path / "project.godot").write_text('[application]\nconfig/name="A game"\n')
    override = tmp_path / "override.cfg"
    legitimate = '[display]\nwindow/size/viewport_width=1280\n'
    override.write_text(legitimate + '; godot-agent-loop: begin interaction server (generated)\n'
                        '[autoload]\nMcpInteractionServer="*res://mcp_interaction_server.gd"\n'
                        '; godot-agent-loop: end interaction server\n')
    assert module.prepare(tmp_path) == {"title": "A game"}
    assert override.read_text() == legitimate
    module.prepare(tmp_path)
    assert override.read_text() == legitimate


def test_godot_import_cache_does_not_change_release_identity(tmp_path):
    project = tmp_path / "project.godot"
    project.write_text("config_version=5\n")
    request = DeployRequest(site_id="game", runtime="godot", source_dir=str(tmp_path))
    manager = DeploymentManagerServer()
    original = manager.source_revision(request)
    (tmp_path / ".godot").mkdir()
    (tmp_path / ".godot/cache").write_bytes(b"temporary import state")
    assert manager.source_revision(request) == original
    project.write_text("config_version=5\n[application]\nconfig/name=\"new\"\n")
    assert manager.source_revision(request) != original


@pytest.mark.asyncio
async def test_upload_error_cannot_publish_a_partial_game(tmp_path):
    (tmp_path / "texture.png").write_bytes(b"asset")

    class BrokenUpload:
        async def write_file(self, path, data):
            raise OSError("upload disconnected")

    with pytest.raises(RuntimeError, match="Failed uploading texture.png"):
        await DeploymentManagerServer()._upload_dir(BrokenUpload(), str(tmp_path), "/app")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_native_preview_through_deploy_tool_and_gateway(tmp_path, monkeypatch):
    """Real browser keys/clicks, large binary upload, redeploy and container cleanup."""
    import asyncio
    import json
    import shutil
    import socket
    import subprocess
    from types import SimpleNamespace

    import uvicorn
    from fastapi import FastAPI
    from playwright.async_api import async_playwright
    from agentevolver.gateway import sites
    from agentevolver.tool.default.deployment import deploy as tool_module

    manager = DeploymentManagerServer()
    await manager.initialize(str(tmp_path / "registry"))
    monkeypatch.setattr(tool_module, "deployment_manager", manager)
    monkeypatch.setattr(sites, "deployment_manager", manager)
    source = tmp_path / "source"
    shutil.copytree(Path(__file__).parent / "fixtures/godot_native", source)
    payload = bytes(range(256)) * 2048  # much larger than Linux's single-argument limit
    (source / "large.bin").write_bytes(payload)
    tool = tool_module.DeployTool()
    ctx = SimpleNamespace(id="preview-test", extra={})
    site_id = "godot-test-" + tmp_path.name[-12:]
    app = FastAPI()
    app.include_router(sites.site_relay)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    container = None

    async def docker(*args):
        return await asyncio.to_thread(subprocess.check_output, ["docker", *args])

    async def state():
        return json.loads(await docker("exec", container, "cat", "/app/observed.json"))

    try:
        result = await tool(action="deploy", site_id=site_id, runtime="godot", backend="docker",
                            source_dir=str(source), ctx=ctx)
        assert result.success, result.message
        container = result.data["resource_id"]
        assert await docker("exec", container, "cat", "/app/large.bin") == payload
        while not server.started:
            await asyncio.sleep(0.05)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(f"http://127.0.0.1:{port}/s/{site_id}--r1/")
            await page.get_by_role("button", name="Start playing").click()
            await page.wait_for_function("document.querySelector('#status').textContent === 'Connected'")
            await page.wait_for_timeout(300)
            before = await state()
            await page.keyboard.down("ArrowRight")
            await page.wait_for_timeout(500)
            await page.keyboard.up("ArrowRight")
            await page.wait_for_timeout(200)
            after = await state()
            assert after["x"] > before["x"] + 0.3
            assert after["held"] is False
            box = await page.locator("canvas").bounding_box()
            await page.mouse.click(box["x"] + box["width"] * 140 / 1280,
                                   box["y"] + box["height"] * 115 / 800)
            await page.wait_for_timeout(300)
            assert (await state())["clicks"] == before["clicks"] + 1
            assert not errors
            await browser.close()
        assert not (source / "observed.json").exists()
        assert (await tool(action="stop", site_id=site_id, ctx=ctx)).success
        assert subprocess.run(["docker", "inspect", container], capture_output=True).returncode != 0
        repeated = await tool(action="redeploy", site_id=site_id, ctx=ctx)
        assert repeated.success, repeated.message
        # Identical source retains its release identity, but gets a fresh instance.
        assert repeated.data["release_number"] == 1
        assert repeated.data["source_revision"] == result.data["source_revision"]
        container = repeated.data["resource_id"]
        assert (await state())["clicks"] == 0
    finally:
        await manager.stop_site(site_id)
        server.should_exit = True
        await serving
        sock.close()
