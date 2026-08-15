"""The Gateway's two transports: what they refuse at the door, and what they release on the way out.

`test_gateway.py` drives the service object through `handle` with no transport in
between, and says so. That leaves this file — the stdio and WebSocket adapters, and the
relay routes that reach a sandbox — as the only place in the repo where the code a real
client actually talks to is exercised. Until the coverage lane was introduced, no test
executed a single line of it.

Three obligations live here and nowhere else. **Authorization happens before the upgrade**,
so a caller with the wrong token never reaches a handler and never costs a subscription.
**A malformed message is answered, not fatal** — one bad line from one client must not end
the loop that is also serving everyone else, and the reply has to distinguish "that was
not JSON" from "that was not a command" or the client cannot tell a bug from a version
skew. And **every exit path releases the event subscription**, because a queue that
outlives its socket is fed forever by a gateway that thinks someone is listening.

The gateway itself is real; only the sandbox upstreams a relay would dial are absent,
which is what the not-found paths below are about.
"""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentevolver.gateway.types import PROTOCOL_VERSION
from agentevolver.gateway.service import AgentGateway
from agentevolver.gateway.transport import create_websocket_app, serve_stdio

#: Any non-empty value works; the tests care about match/mismatch, not the secret.
TOKEN = "a-shared-secret"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def gateway() -> AgentGateway:
    """A real gateway. Constructing one is cheap and needs no session or workspace."""
    return AgentGateway()


@pytest.fixture
def guarded(gateway: AgentGateway) -> TestClient:
    """The app as it is deployed with a token and an origin allowlist configured."""
    return TestClient(create_websocket_app(gateway, token=TOKEN, allowed_origins={ORIGIN}))


# --------------------------------------------------------------------------- #
# The door
# --------------------------------------------------------------------------- #
def test_a_socket_with_the_wrong_token_is_refused_before_it_is_accepted(guarded: TestClient):
    """Refusal has to happen before `accept()`, not after.

    Closing an already-accepted socket looks the same from the browser and is not the
    same here: the code between accept and close subscribes to the event stream, so an
    unauthorized caller that got that far would leave a queue behind on every attempt —
    an unauthenticated way to make the gateway allocate.
    """
    with pytest.raises(WebSocketDisconnect) as refused:
        with guarded.websocket_connect("/ws?token=wrong", headers={"origin": ORIGIN}):
            pass

    # 1008 is "policy violation" — the code a client can act on. A generic 1011 would
    # read as "the server broke", and clients retry those.
    assert refused.value.code == 1008


def test_a_socket_from_an_unlisted_origin_is_refused_even_with_the_right_token(guarded: TestClient):
    """Both checks must pass, not either.

    The token travels in a query string, which lands in browser history and any proxy
    log on the path. The origin check is what stops a page that scraped one from opening
    a socket, so an `or` here instead of an `and` would quietly undo it.
    """
    with pytest.raises(WebSocketDisconnect) as refused:
        with guarded.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": "http://evil.test"}):
            pass

    assert refused.value.code == 1008


def test_the_token_may_arrive_as_a_bearer_header_instead_of_a_query_parameter(gateway: AgentGateway):
    """A client that can set headers should not be forced to put the secret in the URL."""
    client = TestClient(create_websocket_app(gateway, token=TOKEN))

    with client.websocket_connect("/ws", headers={"authorization": f"Bearer {TOKEN}"}) as socket:
        assert socket is not None


def test_a_gateway_with_no_token_configured_lets_anyone_in(gateway: AgentGateway):
    """The default posture, asserted so that it is a decision rather than an accident.

    The gateway binds to loopback, so "no token" is the ordinary single-user case. This
    test exists so that anyone who makes it the *deployed* case has to change a test
    that says out loud what it means.
    """
    client = TestClient(create_websocket_app(gateway))

    with client.websocket_connect("/ws") as socket:
        assert socket is not None


# --------------------------------------------------------------------------- #
# The WebSocket command loop
# --------------------------------------------------------------------------- #
def test_an_unparseable_command_is_answered_and_the_socket_stays_open(gateway: AgentGateway):
    """One client's bad frame must not end its own session, let alone anyone else's.

    Tempting to let the ValidationError propagate — the `finally` would still release
    the subscription, so nothing leaks. But the socket dies, and a UI that sent one
    malformed frame loses the whole conversation instead of seeing an error.
    """
    client = TestClient(create_websocket_app(gateway))

    with client.websocket_connect("/ws") as socket:
        socket.send_text("{}")                      # valid JSON, not a command
        first = json.loads(socket.receive_text())
        assert first["ok"] is False
        assert first["error"]["code"] == "invalid_command"

        # The socket is still usable, which is the actual claim.
        socket.send_text(json.dumps({
            "id": "1", "method": "hello", "params": {},
            "protocol_version": PROTOCOL_VERSION,
        }))
        assert json.loads(socket.receive_text())["id"] == "1"


def test_the_subscription_is_released_when_the_socket_closes(gateway: AgentGateway):
    """A queue that outlives its socket is fed forever by a gateway with no reader.

    Nothing observable breaks at first, which is why this needs its own test: the leak
    shows up much later as unbounded memory on a long-lived gateway that has served many
    short-lived browser tabs.
    """
    client = TestClient(create_websocket_app(gateway))

    with client.websocket_connect("/ws"):
        assert len(gateway._subscribers) == 1

    assert gateway._subscribers == set()


# --------------------------------------------------------------------------- #
# stdio
# --------------------------------------------------------------------------- #
def _run_stdio(gateway: AgentGateway, lines: str) -> list[dict]:
    """Drive `serve_stdio` over a canned stdin and collect what it wrote to stdout."""
    captured = io.StringIO()

    async def drive() -> None:
        import contextlib
        import sys

        original = sys.stdin
        sys.stdin = io.StringIO(lines)
        try:
            with contextlib.redirect_stdout(captured):
                await serve_stdio(gateway)
        finally:
            sys.stdin = original

    asyncio.run(drive())
    return [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]


def test_a_line_that_is_not_json_is_named_as_such_and_the_loop_continues(gateway: AgentGateway):
    """The two failures a client can cause are reported differently, on purpose.

    `invalid_json` means the bytes were mangled — a framing bug, worth retrying.
    `invalid_command` means they arrived intact and said something this server does not
    accept — a version skew, never worth retrying. Collapsing them into one code leaves
    the client guessing, and guessing wrong costs either a hang or a hot loop.
    """
    written = _run_stdio(gateway, "not json at all\n" + json.dumps({}) + "\n")

    assert [message["error"]["code"] for message in written] == ["invalid_json", "invalid_command"]


def test_stdio_answers_a_real_command_and_releases_its_subscription_at_eof(gateway: AgentGateway):
    """EOF on stdin is the normal end of a stdio session, not an error path.

    The unsubscribe sits in a `finally` next to a task cancellation; if the cancel
    raised, the release would be skipped and the queue would be left attached to a
    gateway that has no way to reach this process again.
    """
    command = json.dumps({
        "id": "hello-1", "method": "hello", "params": {},
        "protocol_version": PROTOCOL_VERSION,
    })

    written = _run_stdio(gateway, command + "\n")

    assert [message["id"] for message in written] == ["hello-1"]
    assert gateway._subscribers == set()


# --------------------------------------------------------------------------- #
# Resolve routes — what an unknown id is told
# --------------------------------------------------------------------------- #
def test_health_names_the_protocol_version_it_speaks(guarded: TestClient):
    """The one unauthenticated route. A client that cannot parse a response needs to
    learn *why* without first having to authenticate."""
    body = guarded.get("/health").json()

    assert body == {"ok": True, "protocol_version": PROTOCOL_VERSION}


@pytest.mark.parametrize("route", ["/ide/resolve/no-such-session",
                                   "/science/resolve/no-such-session"])
def test_resolving_an_unknown_session_reveals_nothing_about_it(guarded: TestClient, route: str):
    """404 for "no such session" and 404 for "not yours" are the same answer on purpose.

    These routes hand back a loopback upstream, so a distinguishable response would turn
    them into a way to enumerate which sessions exist and which have a live editor.
    """
    response = guarded.get(route)

    assert response.status_code == 404
    assert response.json()["ok"] is False


def test_a_live_session_resolves_to_the_upstream_its_manager_reports(guarded: TestClient):
    """The happy path, kept honest about who decides.

    The route's whole job is to ask the manager and translate `None` into a 404; the
    address itself is the manager's business. Patching it here is what keeps this a test
    of the route rather than a second test of the IDE lifecycle.
    """
    from agentevolver.ide import ide_manager

    with patch.object(ide_manager, "upstream", return_value="http://127.0.0.1:31337"):
        body = guarded.get("/ide/resolve/live-session").json()

    assert body == {"ok": True, "upstream": "http://127.0.0.1:31337"}


def test_asking_for_a_port_resolves_that_port_and_not_the_editor(guarded: TestClient):
    """`?port=` selects a different lookup entirely, and the two are easy to swap.

    Without a `port` the answer is the editor's own address; with one it is whatever the
    user started in the integrated terminal. Calling the portless form for both would
    point every dev server and OAuth callback at the editor, which fails as a page that
    loads the IDE where the app should be.
    """
    from agentevolver.ide import ide_manager

    async def resolved(session_id: str, port: int) -> str:
        return f"http://127.0.0.1:{port}"

    # A real port, not 0: the route's default is `port: int = 0` and it branches on
    # `if port`, so zero is how "no port was asked for" is spelled — passing it here
    # would exercise the editor branch while looking like it exercised this one.
    with patch.object(ide_manager, "upstream_for_port", side_effect=resolved):
        with patch.object(ide_manager, "upstream", return_value="http://127.0.0.1:THE-EDITOR"):
            body = guarded.get("/ide/resolve/live-session?port=5173").json()

    assert body == {"ok": True, "upstream": "http://127.0.0.1:5173"}


def test_a_started_workstation_resolves_to_its_jupyter_origin(guarded: TestClient):
    """The science route has no `port` form: everything a notebook starts is reached
    through jupyter-server-proxy on the one origin, so there is nothing else to select."""
    from agentevolver.science import science_manager

    with patch.object(science_manager, "upstream", return_value="http://127.0.0.1:8888"):
        body = guarded.get("/science/resolve/a-project").json()

    assert body == {"ok": True, "upstream": "http://127.0.0.1:8888"}


def test_an_unknown_terminal_token_is_refused_before_anything_is_dialled(guarded: TestClient):
    """The token *is* the authorization for this route, so the lookup has to come first.

    If the proxy dialled first and checked after, an attacker could aim the gateway at
    an arbitrary loopback port on the host and read the response — the gateway would be
    doing the connecting, from inside.
    """
    response = guarded.get("/env/term/not-a-real-token/")

    assert response.status_code == 404
    assert response.text == "No such terminal"


# --------------------------------------------------------------------------- #
# The VNC relay
# --------------------------------------------------------------------------- #
def test_a_vnc_relay_refuses_an_unauthorized_socket_too(guarded: TestClient):
    """The relay routes carry their own authorization check, and it is a separate one.

    `/ws` and `/env/vnc` are different endpoints calling the same `_authorize`; a guard
    added to one is not a guard on the other. This route is the more attractive of the
    two to leave open, because it looks like plumbing rather than a command channel —
    but it is the one that bridges straight into a sandbox.
    """
    with pytest.raises(WebSocketDisconnect) as refused:
        with guarded.websocket_connect("/env/vnc?token=wrong", headers={"origin": ORIGIN}):
            pass

    assert refused.value.code == 1008


def test_a_vnc_relay_with_no_live_target_closes_instead_of_hanging(gateway: AgentGateway):
    """A browser that opens a view for a sandbox that never started must be told.

    The alternative is the failure this route was written to end: an accepted socket
    with nothing behind it, which the UI renders as a black canvas indistinguishable
    from a slow one.
    """
    client = TestClient(create_websocket_app(gateway))

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/env/vnc") as socket:
            socket.receive_text()

    assert closed.value.code == 1011


def test_a_named_environment_does_not_fall_back_to_the_most_recent_target(gateway: AgentGateway):
    """The bug the named route exists for: two live views, the second overwriting the first.

    `_latest_vnc_target` keeps exactly one address. With two VNC-capable environments the
    view opened first would silently follow the second one's endpoint, so a request for a
    name that has no target of its own must fail rather than borrow the latest.
    """
    gateway._latest_vnc_target = "ws://127.0.0.1:9999/websockify"
    client = TestClient(create_websocket_app(gateway))

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/env/vnc/an-environment-with-no-view") as socket:
            socket.receive_text()

    assert closed.value.code == 1011
