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
