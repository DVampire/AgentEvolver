"""A deployed site is addressed by name, and the name outlives the deployment.

A deployer asks for a free port each time it runs, so `http://host:PORT` names one
deployment rather than one site: every redeploy hands out a different URL and every link
already given out stops working.

That broke the website scenario's premise directly. Three participants are asked to
return to an ark they visited before, and each round addressed a different port — the
acceptance worker even had to hedge its verdict as being about "the loopback equivalent
of the event URL", because it could not tell whether it had tested the release it was
asked about.

`/s/<site>/` follows the site; `/s/<site>--r<n>/` pins one release.
"""

import os

import pytest

from agentevolver.deploy import deployment_manager
from agentevolver.deploy.types import SiteRecord, SiteStatus


@pytest.fixture
def registered():
    """One running site at release 3, removed again afterwards."""
    deployment_manager._sites["echo-ark"] = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    try:
        yield deployment_manager
    finally:
        deployment_manager._sites.pop("echo-ark", None)


def test_a_name_resolves_to_whatever_port_is_serving_now(registered):
    assert registered.resolve_port("echo-ark") == 8899


def test_a_redeploy_moves_the_port_and_the_name_follows(registered):
    """The whole point: the address survives the port changing underneath it."""
    registered._sites["echo-ark"].port = 9100
    assert registered.resolve_port("echo-ark") == 9100


def test_a_release_address_pins_one_release(registered):
    assert registered.resolve_port("echo-ark--r3") == 8899
    assert registered.resolve_port("echo-ark--r1") is None, (
        "an address for a superseded release must not silently serve the current one"
    )


def test_a_stopped_site_resolves_to_nothing(registered):
    registered._sites["echo-ark"].status = SiteStatus.STOPPED
    assert registered.resolve_port("echo-ark") is None
    assert "echo-ark" not in registered.public_names()


def test_an_unknown_name_resolves_to_nothing(registered):
    assert registered.resolve_port("no-such-ark") is None
    assert registered.resolve_port("echo-ark--rx") is None


def test_the_publish_receipt_carries_the_address_that_outlives_the_release(monkeypatch):
    """Subscribers are told the stable address, not only the port that minted it."""
    from agentevolver.tool.default.deployment.deploy import DeployTool

    monkeypatch.setenv("GATEWAY_PUBLIC_BASE", "http://gw.test:9000")
    rec = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    urls = DeployTool._access_urls(rec)
    assert urls["site_url"] == "http://gw.test:9000/s/echo-ark/"
    assert urls["release_url"] == "http://gw.test:9000/s/echo-ark--r3/"


def test_without_a_gateway_no_address_is_invented(monkeypatch):
    """A URL that resolves nowhere is worse than only offering the port-based one."""
    from agentevolver.tool.default.deployment.deploy import DeployTool

    monkeypatch.delenv("GATEWAY_PUBLIC_BASE", raising=False)
    rec = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    urls = DeployTool._access_urls(rec)
    assert "site_url" not in urls and "release_url" not in urls


@pytest.mark.asyncio
async def test_the_gateway_serves_a_named_site_from_its_own_origin(registered, tmp_path):
    """The relay is the half that makes the name an address rather than a label."""
    import subprocess
    import sys
    import time

    from fastapi.testclient import TestClient

    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.transport import create_websocket_app

    (tmp_path / "index.html").write_text("<h1>ECHO r3</h1>\n", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8899", "--bind", "127.0.0.1"],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):  # the port is the fixture's; wait for it to answer
            time.sleep(0.1)
            try:
                import socket

                with socket.create_connection(("127.0.0.1", 8899), timeout=0.2):
                    break
            except OSError:
                continue

        with TestClient(create_websocket_app(AgentGateway())) as client:
            live = client.get("/s/echo-ark/")
            assert live.status_code == 200
            assert "ECHO r3" in live.text

            pinned = client.get("/s/echo-ark--r3/")
            assert pinned.status_code == 200

            superseded = client.get("/s/echo-ark--r1/")
            assert superseded.status_code == 404

            unknown = client.get("/s/no-such-ark/")
            assert unknown.status_code == 404
            # The 404 names what IS running, so a wrong address is one step from right.
            assert "echo-ark" in unknown.text
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_the_gateway_publishes_the_base_its_own_route_answers_on(monkeypatch):
    """The named URLs were produced by no run at all.

    `/s/<name>/` is served by the gateway, and the base of that address is something only
    the serving process knows — but it was read from an environment variable nothing ever
    set. So every deploy fell through the guard and handed out `host:PORT` addresses that
    die with the release that minted them, which is the exact failure named sites exist to
    fix. Serving the route is what makes the base true, so serving it is what publishes."""
    from agentevolver.cli import GatewayLauncher

    monkeypatch.delenv("GATEWAY_PUBLIC_BASE", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "agentevolver.gateway.transport.create_websocket_app", lambda *a, **k: _StubApp()
    )
    GatewayLauncher("configs/meta_agent.py", transport="websocket",
                    host="127.0.0.1", port=9411)._serve_websocket()

    import os

    assert os.environ["GATEWAY_PUBLIC_BASE"] == "http://127.0.0.1:9411"


def test_an_explicit_base_wins_over_the_gateway_address(monkeypatch):
    """A proxy or hostname in front of this process can only be named from outside it."""
    from agentevolver.cli import GatewayLauncher

    monkeypatch.setenv("GATEWAY_PUBLIC_BASE", "https://ark.example")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "agentevolver.gateway.transport.create_websocket_app", lambda *a, **k: _StubApp()
    )
    GatewayLauncher("configs/meta_agent.py", transport="websocket",
                    host="127.0.0.1", port=9411)._serve_websocket()

    import os

    assert os.environ["GATEWAY_PUBLIC_BASE"] == "https://ark.example"


class _StubApp:
    """Just enough FastAPI surface for the launcher to attach a lifespan to."""

    class _Router:
        lifespan_context = None

    def __init__(self):
        self.router = self._Router()


def test_the_site_relay_is_one_router_not_two_copies():
    """The gateway serves deployed-site names for interactive sessions, and a headless
    run mounts the same router so a script that deploys can hand out names too.

    Two servings of one address would be two chances to disagree about what it means, so
    the route has exactly one definition and both servers include it."""
    from agentevolver.gateway.transport import site_relay

    assert [route.path for route in site_relay.routes] == ["/s/{name}/{path:path}"]


@pytest.mark.asyncio
async def test_a_headless_run_leaves_the_address_to_whoever_already_serves_it(monkeypatch):
    """A gateway already listening owns the address. Claiming it anyway would publish a
    base this run cannot answer on, which is worse than the port-based URLs it replaces."""
    import socket

    from examples.run_meta_agent import serve_deployed_site_names

    monkeypatch.delenv("GATEWAY_PUBLIC_BASE", raising=False)

    class _Taken:
        def bind(self, *a):
            raise OSError("address in use")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Taken())
    assert await serve_deployed_site_names() is None
    assert "GATEWAY_PUBLIC_BASE" not in os.environ
