"""Real base/peer file sharing and host-controller plan reads; no agent or LLM starts."""

import json
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.plan.server import PlanManagerServer
from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.sandbox.server import sandbox_manager
from agentevolver.tool.default.workspace.bash import BashTool


pytestmark = pytest.mark.integration
IMAGE = os.environ.get("AGENTEVOLVER_TEST_IMAGE", "python:3.11-slim")


def test_base_launcher_uses_live_workspace_and_returns_file_ownership(tmp_path):
    if not shutil.which("docker") or subprocess.run(
        ["docker", "image", "inspect", "python:3.11-slim"], capture_output=True,
    ).returncode:
        pytest.skip("requires cached python:3.11-slim")
    repo = Path(__file__).resolve().parents[1]
    for relative in ("scripts/run-in-sandbox.sh", "scripts/sandbox-entry.sh", "docker/base/entrypoint.sh"):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, dest)
    for directory in ("agentevolver", "extension"):
        (tmp_path / directory).mkdir()
    command = (
        'test "$PWD" = /workspace/AgentEvolver && '
        'test "$AGENTEVOLVER_HOME" = "$PWD" && '
        'test "$PYTHONPATH" = "$PWD" && '
        'printf source > agentevolver/probe && printf shared > extension/probe'
    )
    result = subprocess.run([
        "bash", str(tmp_path / "scripts/run-in-sandbox.sh"), "--no-gpus",
        "--image", "python:3.11-slim", "--", "bash", "-c", command,
    ], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    for directory, expected in (("agentevolver", "source"), ("extension", "shared")):
        probe = tmp_path / directory / "probe"
        assert probe.read_text() == expected
        assert probe.stat().st_uid == os.getuid()
        probe.write_text("updated by host")


@pytest.mark.asyncio
async def test_base_peer_and_controller_share_writable_files(tmp_path, monkeypatch):
    if not shutil.which("docker") or subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True,
    ).returncode:
        pytest.skip(f"requires Docker with cached image {IMAGE}")

    sandbox = ProjectSandbox.create(tmp_path / "session", package_root=tmp_path / "package",
                                    shared_extension_root=tmp_path / "shared")
    sandbox.package_root.mkdir()
    sandbox.shared_extension_root.mkdir()
    path_manager.bind_session("local", "mount-roundtrip")
    path_manager.override(P.SESSION_WORKSPACE, sandbox.workspace_root)
    prefix = "/workspace/AgentEvolver"
    keys = [f"mount-probe-{uuid.uuid4().hex}" for _ in range(2)]
    try:
        base = await sandbox_manager.acquire(
            "docker", reuse_key=keys[0], image=IMAGE, network=False, allow_hosts=[], deny_hosts=[],
            user=f"{os.getuid()}:{os.getgid()}", workdir=prefix, mounts={str(tmp_path): prefix},
        )
        # Peers are created by the base, whose paths are not host Docker paths.
        monkeypatch.setenv("AGENTEVOLVER_HOST_ROOT", str(tmp_path))
        monkeypatch.setenv("AGENTEVOLVER_CONTAINER_ROOT", prefix)
        mounts = {prefix + "/" + str(Path(item["source"]).relative_to(tmp_path)):
                  item["target"] for item in sandbox.mounts()}
        peer = await sandbox_manager.acquire(
            "docker", reuse_key=keys[1], image=IMAGE, network=False, allow_hosts=[], deny_hosts=[],
            user=f"{os.getuid()}:{os.getgid()}", workdir="/workspace", mounts=mounts,
        )
        inspection = subprocess.run(["docker", "inspect", peer.resource_id],
                                    capture_output=True, text=True, check=True)
        assert all(mount["RW"] for mount in json.loads(inspection.stdout)[0]["Mounts"])

        monkeypatch.setenv("AGENTEVOLVER_EXEC_CONTAINER", peer.resource_id)
        monkeypatch.setenv("AGENTEVOLVER_TASK_WORKSPACE", str(sandbox.workspace_root))
        monkeypatch.setenv("AGENTEVOLVER_EXEC_WORKDIR", "/workspace")
        plan = PlanManagerServer()
        assert 'path="/workspace/plan.md"' in plan.context("mount-roundtrip", enabled=True)
        for revision in ("Initial plan", "Feedback received: revised plan"):
            response = await BashTool()(command=f"printf %s {shlex.quote(revision)} > /workspace/plan.md")
            assert response.success, response.message
            assert sandbox.workspace_root.joinpath("plan.md").read_text() == revision
            assert revision in plan.context("mount-roundtrip", enabled=True)
            observed = await base.run_command("cat session/workspace/plan.md")
            assert observed.exit_code == 0 and observed.stdout == revision

        for item in sandbox.mounts():
            source = Path(item["source"])
            target = item["target"] + "/write-probe.txt"
            first = await peer.run_command(f"printf peer > {shlex.quote(target)}")
            assert first.exit_code == 0, first.as_message()
            assert (source / "write-probe.txt").read_text() == "peer"
            base_path = prefix + "/" + str(source.relative_to(tmp_path)) + "/write-probe.txt"
            second = await base.run_command(f"printf base > {shlex.quote(base_path)}")
            assert second.exit_code == 0, second.as_message()
            observed = await peer.run_command(f"cat {shlex.quote(target)}")
            assert observed.exit_code == 0 and observed.stdout == "base"
    finally:
        for key in reversed(keys):
            await sandbox_manager.release("docker", reuse_key=key)
