import asyncio
import json
from pathlib import Path

from aiohttp import web
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
import pytest_asyncio

from agentevolver.deploy import DeploymentManagerServer, SiteRecord, SiteStatus
from agentevolver.gateway.sites import site_relay


@pytest_asyncio.fixture
async def upstream(monkeypatch):
    async def echo(request):
        if request.path == "/socket":
            ws = web.WebSocketResponse(protocols=["echo"])
            await ws.prepare(request)
            async for msg in ws:
                if isinstance(msg.data, bytes):
                    await ws.send_bytes(msg.data)
                else:
                    await ws.send_str(msg.data)
            return ws
        if request.path == "/redirect":
            response = web.Response(status=302, headers={"Location": "/login?next=x"})
            response.set_cookie("one", "1", path="/")
            response.set_cookie("two", "2", path="/api")
            return response
        if request.path == "/events":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            await response.write(b"data: second\n\n")
            return response
        return web.json_response(dict(path=request.path, query=request.query_string,
                                      body=await request.text(), prefix=request.headers.get("X-Forwarded-Prefix")))

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", echo)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr("agentevolver.gateway.sites.deployment_manager.refresh", lambda: None)
    monkeypatch.setattr("agentevolver.gateway.sites.deployment_manager.resolve_port", lambda name: port)
    monkeypatch.setattr("agentevolver.gateway.sites.deployment_manager.resolve_url", lambda name: f"http://127.0.0.1:{port}")
    gateway = FastAPI()
    gateway.include_router(site_relay)
    yield gateway
    await runner.cleanup()


@pytest.mark.asyncio
async def test_prefix_post_query_and_redirect_cookies(upstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream), base_url="http://gateway") as client:
        response = await client.post("/s/echo/api/update?x=1", content="payload", headers={"X-Forwarded-Prefix": "/spoof/"})
        assert response.json() == dict(path="/api/update", query="x=1", body="payload", prefix="/s/echo/")
        response = await client.get("/s/echo/redirect")
        assert response.headers["location"] == "/s/echo/login?next=x"
        assert len(response.headers.get_list("set-cookie")) == 2
        assert "Path=/s/echo/" in response.headers.get_list("set-cookie")[0]
        assert "Path=/s/echo/api" in response.headers.get_list("set-cookie")[1]
        response = await client.post("/s/echo?x=1")
        assert response.status_code == 307
        assert response.headers["location"] == "http://gateway/s/echo/?x=1"


@pytest.mark.asyncio
async def test_events_and_encoded_paths(upstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream), base_url="http://gateway") as client:
        response = await client.get("/s/echo/events")
        assert response.text == "data: first\n\ndata: second\n\n"
        assert response.headers["content-type"].startswith("text/event-stream")
        response = await client.get("/s/echo/a%23b?value=x%26y")
        assert response.json()["path"] == "/a#b"


@pytest.mark.asyncio
async def test_websocket_text_binary_and_protocol(upstream):
    def connect():
        with TestClient(upstream) as client:
            with client.websocket_connect("/s/echo/socket", subprotocols=["echo"]) as socket:
                assert socket.accepted_subprotocol == "echo"
                socket.send_text("hello")
                assert socket.receive_text() == "hello"
                socket.send_bytes(b"binary")
                assert socket.receive_bytes() == b"binary"
    await asyncio.wait_for(asyncio.to_thread(connect), 10)


def test_registry_writers_preserve_unrelated_changes(tmp_path):
    path = tmp_path / "sites.json"
    path.write_text("{}")
    a, b = DeploymentManagerServer(), DeploymentManagerServer()
    for manager in (a, b):
        manager._registry_path = str(path)
        manager._load()
    a._sites["first"] = SiteRecord(site_id="first", runtime="static", status=SiteStatus.RUNNING, port=8001)
    a._save()
    b._sites["second"] = SiteRecord(site_id="second", runtime="static", status=SiteStatus.RUNNING, port=8002)
    b._save()
    assert set(json.loads(path.read_text())) == {"first", "second"}
    b.refresh()
    b._sites["first"].status = SiteStatus.STOPPED
    b._save()
    a._save()  # unchanged stale first record must not resurrect it
    a.refresh()
    assert a.resolve_port("first") is None
    assert a.resolve_port("second") == 8002


def test_public_url_identity(monkeypatch):
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE", "https://agents.example.com/")
    site = SiteRecord(site_id="echo", runtime="static", port=45678, release_number=3)
    assert DeploymentManagerServer.public_urls(site) == {
        "site_url": "https://agents.example.com/s/echo/",
        "release_url": "https://agents.example.com/s/echo--r3/",
    }


def test_public_pages_distinguish_deployment_and_status_time(monkeypatch):
    manager = DeploymentManagerServer()
    monkeypatch.setattr(manager, "refresh", lambda: None)
    manager._sites["legacy"] = SiteRecord(site_id="legacy", runtime="static",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-05T03:00:00+00:00")
    manager._sites["new"] = SiteRecord(site_id="new", runtime="static", status=SiteStatus.STOPPED,
        deployed_at="2026-09-05T02:00:00+00:00", updated_at="2026-09-05T03:00:00+00:00")
    pages = {page["name"]: page for page in manager.public_pages()}
    assert pages["legacy"]["deployed_at"] == ""
    assert pages["legacy"]["created_at"] == "2026-09-01T00:00:00+00:00"
    assert pages["new"]["deployed_at"] == "2026-09-05T02:00:00+00:00"
    assert pages["new"]["updated_at"] == "2026-09-05T03:00:00+00:00"


def test_backend_exposure_and_gateway_paths():
    from agentevolver.gateway.sites import target_url
    manager = DeploymentManagerServer()
    manager._sites["container"] = SiteRecord(site_id="container", runtime="custom",
        status=SiteStatus.RUNNING, port=8000, url="https://sandbox.example/expose/abc/", release_number=2)
    assert manager.resolve_url("container--r2") == "https://sandbox.example/expose/abc/"
    assert manager.resolve_url("container--r1") is None
    assert target_url(manager.resolve_url("container"), "api/data", "x=1") == "https://sandbox.example/expose/abc/api/data?x=1"
