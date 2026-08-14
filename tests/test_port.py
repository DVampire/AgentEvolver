"""One place that knows which port is bound to what, and keeps knowing after a restart.

Ports were scattered literals before this registry existed: the Gateway's in one module,
the UI's in a shell script, a deploy site's chosen at random and remembered nowhere. The
registry is what makes them de-conflicted and discoverable, and the two properties that
carry that weight are (a) a name keeps its port across calls and across processes, so a
restart does not move a service somebody is already connected to, and (b) the picture is
written to ``ports.json``, so a second reader sees what the first one bound.

Everything else here is about the ways this fails quietly. A port handed out while it is
already in use produces a service that dies on bind, minutes after the registry said it
was fine. A record handed out by reference lets a caller edit the registry by accident. And
a caller that passes the wrong keyword gets a ``TypeError`` that three separate call sites
have swallowed — the last test in this file exists because of them.
"""

import json
import socket

import pytest

from agentevolver.port.server import (
    GATEWAY,
    OPENSANDBOX,
    UI,
    PortManager,
    is_free,
    os_free_port,
)


@pytest.fixture
def ports_file(tmp_path, monkeypatch):
    """Point ``P.PORTS`` at a throwaway file so real load/save logic runs."""
    path = tmp_path / "ports.json"
    monkeypatch.setattr(
        "agentevolver.port.server.path_manager.get",
        lambda key, create=False: path,
    )
    return path


@pytest.fixture
def registry(ports_file):
    """A fresh manager — the global singleton caches its path across tests."""
    return PortManager()


# --------------------------------------------------------------------------- #
# Which port a name gets
# --------------------------------------------------------------------------- #
def test_an_allocated_port_is_reused_for_the_same_name(registry):
    """Registering is how a caller *looks up* its own port, not just how it claims one.

    A second call that allocated afresh would move the service on every restart, and
    anything holding the earlier number — a saved URL, a running browser — would be
    pointing at nothing.
    """
    first = registry.register("deploy-site")
    second = registry.register("deploy-site")
    assert first["port"] == second["port"]


def test_override_forces_a_fresh_allocation(registry):
    """The escape hatch from that idempotence, for a name whose old port is now taken."""
    first = registry.register("deploy-site")
    again = registry.register("deploy-site", override=True)
    # A fresh OS-assigned port; the record must at least have been rewritten.
    assert again["updated_at"] >= first["updated_at"]
    assert registry.get("deploy-site") == again["port"]


def test_an_explicit_port_is_recorded_verbatim_every_time(registry):
    """Known bindings (the Gateway's) are refreshed, not treated as idempotent.

    An explicit port is a statement of where the service *is*, so the registry has to
    follow it. Reusing the stored value instead would leave the file describing the last
    run's Gateway while the current one listens somewhere else.
    """
    registry.register("gateway", GATEWAY)
    assert registry.get("gateway") == GATEWAY
    registry.register("gateway", GATEWAY + 1)
    assert registry.get("gateway") == GATEWAY + 1


def test_a_free_preferred_port_is_honoured(registry):
    """A preference is honoured when it can be: services have conventional ports, and
    silently ignoring the request would make every restart land somewhere new."""
    wanted = os_free_port()
    assert registry.register("prefers", preferred=wanted)["port"] == wanted


def test_a_taken_preferred_port_falls_back_to_a_free_one(registry):
    """The socket is held open for the whole registration, so the port is genuinely in
    use at the moment the registry decides — not merely known to have been.

    Handing it out anyway would produce a record that looks correct and a service that
    dies on bind some minutes later, once the build finishes and the server starts.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        record = registry.register("collides", preferred=taken)
    assert record["port"] != taken
    assert record["preferred"] == taken  # the intent is kept for readability


def test_unregister_reports_whether_it_had_anything_to_drop(registry):
    """Callers unregister on teardown paths that may run twice; the boolean is what tells
    a genuine release from a repeat."""
    registry.register("temporary", 4321)
    assert registry.unregister("temporary") is True
    assert registry.unregister("temporary") is False
    assert registry.get("temporary") is None


def test_an_unknown_name_reads_as_none_rather_than_raising(registry):
    """Lookups happen before the thing is bound — a UI asking where the Gateway is may
    ask before the Gateway starts. That is an answer of "not yet", not an error."""
    assert registry.get("never-registered") is None
    assert registry.get_info("never-registered") is None


# --------------------------------------------------------------------------- #
# What survives to disk, and what callers may hold
# --------------------------------------------------------------------------- #
def test_allocations_are_persisted_for_the_next_process(registry, ports_file):
    """The registry's whole point is cross-process visibility.

    The UI, the Gateway and a deploy each run in their own process; ``ports.json`` is the
    only thing they share. Kept in memory only, the file is a snapshot of whichever
    process happened to write last.
    """
    registry.register("gateway", GATEWAY, type="host")
    on_disk = json.loads(ports_file.read_text())
    assert on_disk["gateway"]["port"] == GATEWAY
    assert on_disk["gateway"]["type"] == "host"

    # A second process reads the same picture through a fresh manager.
    assert PortManager().get("gateway") == GATEWAY


def test_listing_hands_out_copies_not_the_live_records(registry):
    """A listing is usually built for display, and display code edits what it is given.

    Handing out the live dicts makes those edits into registry writes — persisted on the
    next save, with nothing in between that looks like a mutation.
    """
    registry.register("gateway", GATEWAY)
    listed = registry.list()
    listed["gateway"]["port"] = 1
    assert registry.get("gateway") == GATEWAY


def test_get_info_hands_out_a_copy_too(registry):
    """The same guarantee on the single-record path, which is easy to miss when only the
    listing is written defensively."""
    registry.register("gateway", GATEWAY)
    registry.get_info("gateway")["port"] = 1
    assert registry.get("gateway") == GATEWAY


# --------------------------------------------------------------------------- #
# Facts the rest of the codebase depends on
# --------------------------------------------------------------------------- #
def test_the_well_known_ports_are_distinct():
    """These are the single source of truth; a collision would be silent breakage.

    Two constants sharing a value means two services fight over one port, and whichever
    starts second fails to bind — pointing at the service, not at the constant.
    """
    assert len({GATEWAY, OPENSANDBOX, UI}) == 3


def test_is_free_reflects_an_actually_bound_socket():
    """Checked against a real listening socket rather than a mock, because the whole
    value of this helper is that it agrees with the operating system."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        assert is_free(held.getsockname()[1]) is False


def test_nothing_calls_register_with_the_wrong_keyword() -> None:
    """`register(..., kind=...)` raises TypeError; the parameter is `type`.

    Three separate call sites have had this wrong. Each failed differently and none
    failed loudly: `scripts/serve-ui.sh` swallowed it with `|| true` so the UI port
    silently never entered the registry, and the computer sandbox's `vnc_ws_url` raised
    inside a `try` that the environment's `live_view` caught, so the frontend reported
    "this environment has no live view" — which reads as unimplemented rather than as a
    typo one frame down.
    """
    import inspect
    import re
    from pathlib import Path

    from agentevolver.port import port_manager

    assert "type" in inspect.signature(port_manager.register).parameters
    assert "kind" not in inspect.signature(port_manager.register).parameters

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in [*root.glob("agentevolver/**/*.py"), *root.glob("scripts/*.sh"),
                 *root.glob("examples/*.py")]:
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"port_manager\.register\([^)]*", text):
            if "kind=" in match.group(0):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"port_manager.register(kind=...) should be type=...: {offenders}"
