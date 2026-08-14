"""Where a sandbox may reach is decided outside it, and every attempt is on the record.

The claim these protect is "the work in this sandbox had no internet access" — which a
results file can only assert if a missing rule fails closed, a denial cannot be talked
around from inside, and the attempts were counted. The enforcement lives on the host for
that reason: the sandbox gets no interface at all and one mounted socket, so an unlisted
host is unreachable rather than filtered.

The failures this catches are quiet ones. A rule that parses wrong matches nothing and
looks fine in the config file; a relay left running after its sandbox is gone is a
listener for something that no longer exists; an audit that returns `{}` instead of
`None` reports isolation that was never applied. None of those change what a run does
until the day someone reads the run and believes it.
"""

import asyncio
import os
import socket

import pytest

from agentevolver.sandbox.netpolicy import NetworkPolicy, model_endpoint_hosts
from agentevolver.sandbox.relay import EgressRelay, _parse_target, default_socket_path
from agentevolver.sandbox.types import SandboxConfig


# ------------------------------------------------------------ what a rule matches
def test_unmatched_host_is_denied_when_the_network_is_closed():
    """Forgetting to list a host must fail closed, not open."""
    policy = NetworkPolicy(allow=["api.example.com"], default_allow=False)
    assert policy.decide("api.example.com", 443).allowed is True
    assert policy.decide("github.com", 443).allowed is False


def test_deny_beats_allow():
    """The lists are written at different times by different people. `allow` tends to be
    broad — a wildcard someone added to unblock themselves — and `deny` is the specific
    exception carved out of it, so the order of evaluation decides whether the exception
    means anything. The reason is asserted too: a denial the operator cannot trace back
    to a rule cannot be corrected.
    """
    policy = NetworkPolicy(allow=["*.example.com"], deny=["evil.example.com"], default_allow=True)
    assert policy.decide("api.example.com", 443).allowed is True
    decision = policy.decide("evil.example.com", 443)
    assert decision.allowed is False
    assert "denied by rule" in decision.reason


def test_wildcard_covers_subdomains_but_not_the_bare_domain():
    """`*.example.com` naming example.com itself would quietly widen every allowlist
    written with a wildcard."""
    policy = NetworkPolicy(allow=["*.example.com"], default_allow=False)
    assert policy.decide("api.example.com", 443).allowed is True
    assert policy.decide("a.b.example.com", 443).allowed is True
    assert policy.decide("example.com", 443).allowed is False


def test_a_port_pinned_rule_does_not_leak_to_other_ports():
    """Naming a port is the operator saying "this host, for this service". Ignoring the
    port once a rule carries one turns every pinned entry into a bare host entry, and the
    config still reads as though the restriction is in force."""
    policy = NetworkPolicy(allow=["api.example.com:443"], default_allow=False)
    assert policy.decide("api.example.com", 443).allowed is True
    assert policy.decide("api.example.com", 8080).allowed is False


def test_host_matching_ignores_case_and_a_trailing_dot():
    """A fully-qualified name with the root dot is the same host; a resolver that adds
    one must not become a way around the list."""
    policy = NetworkPolicy(allow=["api.example.com"], default_allow=False)
    assert policy.decide("API.Example.COM.", 443).allowed is True


def test_an_ipv6_literal_is_not_mangled_into_a_port():
    """`2001:db8::1`.rpartition(":") ends in "1", a digit — taking that as a port turns
    the host into `2001:db8:` and the rule silently matches nothing."""
    policy = NetworkPolicy(allow=["2001:db8::1"], default_allow=False)
    assert policy.decide("2001:db8::1", 443).allowed is True
    assert policy.decide("2001:db8::1", 80).allowed is True

    # A port on an IPv6 address has to use the standard bracketed form.
    pinned = NetworkPolicy(allow=["[2001:db8::1]:443"], default_allow=False)
    assert pinned.decide("2001:db8::1", 443).allowed is True
    assert pinned.decide("2001:db8::1", 80).allowed is False


def test_a_sandbox_config_becomes_the_policy_it_describes():
    """`network` is not one more rule: it decides what happens to everything unmatched.

    Off, the allowlist is the only way out; on, the lists are exceptions to an otherwise
    open connection. The two derived properties are what the manager switches on — a
    policy that blocks everything or restricts nothing needs no relay standing behind it,
    and getting either wrong leaves a process serving a sandbox that cannot use it.
    """
    config = SandboxConfig(network=False, allow_hosts=["api.example.com"], deny_hosts=["x.com"])
    policy = NetworkPolicy.from_config(config)
    assert policy.decide("api.example.com", 443).allowed is True
    assert policy.decide("anything.else", 443).allowed is False
    assert policy.blocks_everything is False

    assert NetworkPolicy.from_config(SandboxConfig(network=False)).blocks_everything is True
    assert NetworkPolicy.from_config(SandboxConfig(network=True)).is_unrestricted is True


def test_model_hosts_come_from_the_environment_not_a_hardcoded_provider():
    """A deployment that overrides the provider base URL is the case that matters: this
    one routes OpenRouter traffic to a private gateway, so an allowlist naming
    openrouter.ai would look correct and block every model call."""
    hosts = model_endpoint_hosts({
        "OPENROUTER_API_BASE": "https://api.private-gateway.example/v1",
        "GOOGLE_API_BASE": "https://generativelanguage.googleapis.com",
        "UNRELATED": "https://nope.example",
    })
    assert "api.private-gateway.example" in hosts
    assert "generativelanguage.googleapis.com" in hosts
    assert "nope.example" not in hosts
    # With nothing configured, fall back to the public endpoints rather than an empty
    # allowlist, which would block every model call.
    assert "openrouter.ai" in model_endpoint_hosts({})


# ------------------------------------------------- what the relay does with a request
def test_both_proxy_request_forms_name_the_same_host():
    """A proxy sees two shapes: `CONNECT host:port` for tunnelled TLS, and an absolute
    URI for plain HTTP. Only reading one of them leaves the other with no host to decide
    about — and a request whose host cannot be determined is a request the policy never
    sees. The relative form is the third case, and it must yield nothing rather than
    guess: it names a path on a server the proxy was never told about.
    """
    assert _parse_target("CONNECT api.example.com:443 HTTP/1.1") == ("api.example.com", 443, "CONNECT")
    assert _parse_target("CONNECT api.example.com HTTP/1.1") == ("api.example.com", 443, "CONNECT")
    assert _parse_target("GET http://api.example.com/x HTTP/1.1") == ("api.example.com", 80, "GET")
    assert _parse_target("GET https://api.example.com/x HTTP/1.1") == ("api.example.com", 443, "GET")
    assert _parse_target("GET /relative HTTP/1.1")[0] is None


def test_socket_path_stays_inside_the_af_unix_limit():
    """AF_UNIX allows 108 bytes including the terminator, and a session directory carrying
    an owner plus a ~30-character instance id overruns it. The failure is a bare
    'AF_UNIX path too long' at bind time, which points nowhere."""
    path = default_socket_path("abishekvashok__cmatrix.5c082c6")
    assert len(str(path)) <= 100
    # Distinct keys that share a prefix must not collide on one socket.
    a = default_socket_path("very-long-instance-name-that-is-truncated-aaaa")
    b = default_socket_path("very-long-instance-name-that-is-truncated-bbbb")
    assert a != b


def test_relay_refuses_an_overlong_socket_path(tmp_path):
    """Refusing at start beats letting bind() do it. The error says which limit was hit
    and where the path should come from instead; the kernel's says "AF_UNIX path too
    long", which points at nothing and arrives after the sandbox is half built."""
    policy = NetworkPolicy(allow=[], default_allow=False)
    too_long = tmp_path / ("d" * 120) / "egress.sock"
    with pytest.raises(ValueError, match="Unix socket"):
        asyncio.run(EgressRelay(too_long, policy).start())


def _serve_once(payload: bytes) -> tuple[str, int, socket.socket]:
    """A throwaway TCP server that answers one connection, for the allowed path."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return "127.0.0.1", listener.getsockname()[1], listener


def test_relay_denies_an_unlisted_host_and_records_it():
    """The refusal has to reach the caller as an HTTP error it can read, and land in the
    audit trail. A denial that only appears in a log is not evidence."""
    async def scenario():
        policy = NetworkPolicy(allow=["allowed.example"], default_allow=False)
        relay = EgressRelay(default_socket_path("test-deny"), policy)
        await relay.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(relay.socket_path))
            writer.write(b"CONNECT blocked.example:443 HTTP/1.1\r\nHost: blocked.example\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
            writer.close()
            return response, relay.audit()
        finally:
            await relay.stop()

    response, audit = asyncio.run(scenario())
    assert b"403" in response
    # The message must say retrying will not help, or a blocked host reads as a flaky
    # network and gets retried until the step budget is gone.
    assert b"will\nnot work" in response or b"not work" in response
    assert audit["attempts"] == 1
    assert audit["denied"][0]["host"] == "blocked.example"


def test_relay_tunnels_an_allowed_host():
    """The other half of the barrier, and the easier one to get wrong quietly: a relay
    that denied everything would pass every refusal test in this file while leaving the
    agent brain unable to reach the model endpoint. Bytes are pushed through the tunnel
    rather than stopping at the 200, because a connection that establishes and then
    carries nothing looks identical to a working one until a request is made.
    """
    async def scenario():
        host, port, listener = _serve_once(b"")
        listener.setblocking(False)
        policy = NetworkPolicy(allow=[f"{host}:{port}"], default_allow=False)
        relay = EgressRelay(default_socket_path("test-allow"), policy)
        await relay.start()
        loop = asyncio.get_running_loop()
        try:
            reader, writer = await asyncio.open_unix_connection(str(relay.socket_path))
            writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            accepted, _ = await asyncio.wait_for(loop.sock_accept(listener), timeout=5)
            established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)

            # Bytes must flow end to end, not merely connect.
            writer.write(b"ping")
            await writer.drain()
            accepted.setblocking(False)
            received = await asyncio.wait_for(loop.sock_recv(accepted, 16), timeout=5)

            accepted.close()
            listener.close()
            writer.close()
            return established, received, relay.audit()
        finally:
            await relay.stop()

    established, received, audit = asyncio.run(scenario())
    assert b"200" in established
    assert received == b"ping"
    assert audit["denied"] == []


def test_relay_reports_an_unreachable_allowed_host_as_a_gateway_error():
    """A permitted host that is simply down must not be recorded as a policy denial, or
    the audit trail stops distinguishing 'we blocked this' from 'the network broke'."""
    async def scenario():
        # Port 1 on loopback: allowed by policy, nothing listening.
        policy = NetworkPolicy(allow=["127.0.0.1:1"], default_allow=False)
        relay = EgressRelay(default_socket_path("test-unreachable"), policy)
        await relay.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(relay.socket_path))
            writer.write(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=10)
            writer.close()
            return response, relay.audit()
        finally:
            await relay.stop()

    response, audit = asyncio.run(scenario())
    assert b"502" in response
    assert audit["denied"] == []
    assert audit["allowed_hosts"] == ["127.0.0.1"]


def test_forwarder_says_so_when_there_is_no_relay():
    """Without a relay socket the sandbox has no route out at all. Saying that beats an
    opaque connection error that invites retries which can never succeed."""
    from agentevolver.sandbox.forwarder import Forwarder

    async def scenario():
        forwarder = Forwarder("/nonexistent/egress.sock", port=0)
        server = await asyncio.start_server(forwarder._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT x.example:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=5)
        writer.close()
        server.close()
        await server.wait_closed()
        return response

    response = asyncio.run(scenario())
    assert b"502" in response
    assert b"no egress relay" in response


# ------------------------------------ one declared policy, applied to every sandbox
#
# The policy is declared once in the run's config and the manager applies it to every
# sandbox it hands out. An isolation mechanism that depends on each call site remembering
# to wire it up is one that will be missing somewhere.

class _StubSandbox:
    """Minimal registered backend: records the config it was handed."""

    def __init__(self, config):
        self.config = config

    async def start(self):
        return self

    async def destroy(self):
        return None

    async def is_alive(self):
        return True


def _manager(**configured):
    """A manager whose backend is a stub and whose config values are already resolved,
    the way they are after initialize() runs against a real config file."""
    from agentevolver.sandbox.server import SandboxManagerServer

    manager = SandboxManagerServer()
    manager._initialized = True
    manager.allow_hosts = list(configured.get("allow_hosts", []))
    manager.deny_hosts = list(configured.get("deny_hosts", []))

    async def _get(_kind):
        return _StubSandbox

    manager.get = _get  # type: ignore[assignment]
    return manager


def test_configured_policy_applies_to_every_sandbox():
    """Declared once in the run's config, applied by the manager, never by a call site.

    The stamped `egress_socket` is the load-bearing part: it is how the backend learns
    there is anything to attach. A policy that reached the config object but not the
    socket would produce a sandbox whose recorded policy and actual reachability
    disagree, and the recorded one is what a results file would report.
    """
    manager = _manager(allow_hosts=["api.example.com"], deny_hosts=["github.com"])

    async def scenario():
        return await manager.acquire("stub", reuse_key="a", network=False)

    handle = asyncio.run(scenario())
    try:
        assert handle.config.allow_hosts == ["api.example.com"]
        assert handle.config.deny_hosts == ["github.com"]
        # A policy that needs enforcing got a relay socket stamped in for the backend.
        assert handle.config.egress_socket is not None
        assert manager.egress_audit(handle)["policy"] == (
            "network: closed, allow=['api.example.com'], deny=['github.com']"
        )
    finally:
        asyncio.run(manager.cleanup())


def test_an_acquire_can_override_the_run_default():
    """One sandbox needing GitHub blocked while another needs it is the reason these are
    per-sandbox and not a global switch."""
    manager = _manager(deny_hosts=["github.com"])

    async def scenario():
        strict = await manager.acquire("stub", reuse_key="strict", network=True)
        relaxed = await manager.acquire("stub", reuse_key="relaxed", network=True, deny_hosts=[])
        return strict, relaxed

    strict, relaxed = asyncio.run(scenario())
    try:
        assert strict.config.deny_hosts == ["github.com"]
        assert relaxed.config.deny_hosts == []
        # Nothing left to enforce for the relaxed one, so it gets no relay at all.
        assert relaxed.config.egress_socket is None
        assert manager.egress_audit(relaxed) is None
    finally:
        asyncio.run(manager.cleanup())


def test_no_relay_when_there_is_nothing_to_enforce():
    """An open network has no rules; a fully closed one has no interface to police. A
    relay in either case is a process doing nothing."""
    manager = _manager()

    async def scenario():
        return (
            await manager.acquire("stub", reuse_key="open", network=True),
            await manager.acquire("stub", reuse_key="closed", network=False),
        )

    wide_open, airgapped = asyncio.run(scenario())
    try:
        assert wide_open.config.egress_socket is None
        assert airgapped.config.egress_socket is None
        # None, not {}: an unrestricted sandbox is not watched, and an empty denial list
        # would read as evidence of isolation that was never applied.
        assert manager.egress_audit(wide_open) is None
        assert manager.egress_audit(airgapped) is None
    finally:
        asyncio.run(manager.cleanup())


def test_releasing_a_sandbox_stops_its_relay():
    """A relay is a live proxy to its allowlist, reachable by anything on the host that
    can open the socket. Leaving one behind after its sandbox is gone accumulates
    listeners across a long run, and the audit it still answers describes a container
    that no longer exists."""
    manager = _manager(allow_hosts=["api.example.com"])

    async def scenario():
        handle = await manager.acquire("stub", reuse_key="temp", network=False)
        socket_path = handle.config.egress_socket
        assert os.path.exists(socket_path)
        await manager.release("stub", reuse_key="temp")
        return socket_path, manager.egress_audit(handle)

    socket_path, audit_after = asyncio.run(scenario())
    assert not os.path.exists(socket_path), "relay socket outlived the sandbox"
    assert audit_after is None


def test_two_sandboxes_get_separate_relays():
    """One socket shared by two sandboxes merges their audit trails, so neither run can
    say what *it* reached — and releasing either one takes the other's route out with
    it."""
    manager = _manager(allow_hosts=["api.example.com"])

    async def scenario():
        return (
            await manager.acquire("stub", reuse_key="one", network=False),
            await manager.acquire("stub", reuse_key="two", network=False),
        )

    one, two = asyncio.run(scenario())
    try:
        assert one.config.egress_socket != two.config.egress_socket
    finally:
        asyncio.run(manager.cleanup())
