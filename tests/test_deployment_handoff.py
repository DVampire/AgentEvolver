"""Published URLs survive a launcher's teardown without retaining task resources."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
import httpx
import pytest

from agentevolver.deploy.server import DeploymentManagerServer
from agentevolver.deploy.types import DeployRequest, SiteRecord, SiteStatus
from agentevolver.gateway import sites


@pytest.mark.asyncio
async def test_published_archive_survives_launcher_exit_and_path_change(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("published bytes")
    (source / "server.py").write_text(
        "import os\nfrom http.server import HTTPServer, SimpleHTTPRequestHandler\n"
        "HTTPServer(('127.0.0.1', int(os.environ['PORT'])), SimpleHTTPRequestHandler).serve_forever()\n"
    )
    registry = tmp_path / "sites.json"
    script = """
import asyncio, sys
import agentevolver.deploy.default
from agentevolver.deploy.server import DeploymentManagerServer
from agentevolver.deploy.types import DeployRequest
from agentevolver.sandbox import sandbox_manager
async def main():
    manager = DeploymentManagerServer()
    manager._registry_path = sys.argv[1]
    manager._initialized = True
    try:
        for name, stage in [('published', 'published'), ('preview', 'preview')]:
            record = await manager.deploy(DeployRequest(
                site_id=name, stage=stage, runtime='custom', backend='host', source_dir=sys.argv[2],
                overrides={'start': 'python server.py', 'health': {'timeout_s': 5, 'interval_s': .05}}))
            assert record.status.value == 'running', record.error
    finally:
        await manager.cleanup()
        await sandbox_manager.cleanup()
asyncio.run(main())
"""
    env = {**os.environ, "PATH": "/usr/bin:/bin", "AGENTEVOLVER_HOME": str(tmp_path / "home"),
           "UNRELATED_PRIVATE_VALUE": "do not archive this"}
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(registry), str(source)],
                                     env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr[-2500:]
    records = json.loads(registry.read_text())
    assert records["published"]["status"] == "detached"
    assert records["preview"]["status"] == "stopped"
    assert records["published"]["resource_id"] is None
    saved_env = records["published"]["request"]["env"]
    assert os.path.dirname(sys.executable) in saved_env["PATH"].split(os.pathsep)
    assert "UNRELATED_PRIVATE_VALUE" not in saved_env

    # Author changes after publication must not change the archived deliverable.
    (source / "index.html").write_text("unpublished edit")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    manager = DeploymentManagerServer()
    manager._registry_path = str(registry)
    manager._initialized = True
    manager._load()
    monkeypatch.setattr(sites, "deployment_manager", manager)
    app = FastAPI()
    app.include_router(sites.site_relay)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            response = await client.get("/s/published/")
            assert response.status_code == 200, response.text
            assert response.text == "published bytes"
            assert manager._sites["published"].release_number == 1
            assert (await client.get("/s/published--r1/")).text == "published bytes"
            assert (await client.get("/s/preview/")).status_code == 404
            await manager.stop_site("published")
            assert (await client.get("/s/published/")).status_code == 404
    finally:
        for name in list(manager._owned_sites):
            await manager.stop_site(name)


@pytest.mark.asyncio
async def test_failed_deployment_is_unavailable_not_unknown(monkeypatch):
    manager = DeploymentManagerServer()
    manager._sites["broken"] = SiteRecord(site_id="broken", runtime="custom", status=SiteStatus.FAILED,
                                          error="sensitive internal command")
    monkeypatch.setattr(sites, "deployment_manager", manager)

    async def missing(_name):
        return None

    monkeypatch.setattr(sites, "site_target", missing)
    app = FastAPI()
    app.include_router(sites.site_relay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        failed = await client.get("/s/broken/")
        assert failed.status_code == 503
        assert "server log" in failed.text and "sensitive" not in failed.text
        assert (await client.get("/s/unknown/")).status_code == 404
