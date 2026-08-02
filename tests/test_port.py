"""Port registry: allocation, idempotence, and what survives to ``ports.json``.

The registry exists so dynamic bindings are de-conflicted and discoverable
rather than scattered literals. The two properties that carry that weight are
(a) a name keeps its port across calls and processes, and (b) the picture is
persisted, so a second reader sees what the first one bound.
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


def test_an_allocated_port_is_reused_for_the_same_name(registry):
    first = registry.register("deploy-site")
    second = registry.register("deploy-site")
    assert first["port"] == second["port"]


def test_override_forces_a_fresh_allocation(registry):
    first = registry.register("deploy-site")
    again = registry.register("deploy-site", override=True)
    # A fresh OS-assigned port; the record must at least have been rewritten.
    assert again["updated_at"] >= first["updated_at"]
    assert registry.get("deploy-site") == again["port"]


def test_an_explicit_port_is_recorded_verbatim_every_time(registry):
    """Known bindings (the Gateway's) are refreshed, not treated as idempotent."""
    registry.register("gateway", GATEWAY)
    assert registry.get("gateway") == GATEWAY
    registry.register("gateway", GATEWAY + 1)
    assert registry.get("gateway") == GATEWAY + 1


def test_a_free_preferred_port_is_honoured(registry):
    wanted = os_free_port()
    assert registry.register("prefers", preferred=wanted)["port"] == wanted


def test_a_taken_preferred_port_falls_back_to_a_free_one(registry):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        record = registry.register("collides", preferred=taken)
    assert record["port"] != taken
    assert record["preferred"] == taken  # the intent is kept for readability


def test_unregister_reports_whether_it_had_anything_to_drop(registry):
    registry.register("temporary", 4321)
    assert registry.unregister("temporary") is True
    assert registry.unregister("temporary") is False
    assert registry.get("temporary") is None


def test_an_unknown_name_reads_as_none_rather_than_raising(registry):
    assert registry.get("never-registered") is None
    assert registry.get_info("never-registered") is None


def test_allocations_are_persisted_for_the_next_process(registry, ports_file):
    registry.register("gateway", GATEWAY, type="host")
    on_disk = json.loads(ports_file.read_text())
    assert on_disk["gateway"]["port"] == GATEWAY
    assert on_disk["gateway"]["type"] == "host"

    # A second process reads the same picture through a fresh manager.
    assert PortManager().get("gateway") == GATEWAY


def test_listing_hands_out_copies_not_the_live_records(registry):
    registry.register("gateway", GATEWAY)
    listed = registry.list()
    listed["gateway"]["port"] = 1
    assert registry.get("gateway") == GATEWAY


def test_get_info_hands_out_a_copy_too(registry):
    registry.register("gateway", GATEWAY)
    registry.get_info("gateway")["port"] = 1
    assert registry.get("gateway") == GATEWAY


def test_the_well_known_ports_are_distinct():
    """These are the single source of truth; a collision would be silent breakage."""
    assert len({GATEWAY, OPENSANDBOX, UI}) == 3


def test_is_free_reflects_an_actually_bound_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        assert is_free(held.getsockname()[1]) is False
