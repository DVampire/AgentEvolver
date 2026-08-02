"""The opensandbox daemon's port is claimed when the daemon is needed, not before.

Resolving the daemon's domain registers a port in the machine-level registry —
state shared by every process on the host. Most processes that build a
``SandboxServerManager`` never start a daemon: an environment roster with nothing
marked ``use_sandbox``, or a run on the ``docker``/``host`` backend. Claiming the
port at construction published a record saying opensandbox held a port nothing
was listening on, which is precisely the claim a "who holds what" registry must
not make.
"""

import pytest

from agentevolver.sandbox.process import SandboxServerManager


@pytest.fixture
def registry_writes(monkeypatch):
    """Record every port registration instead of touching the real registry."""
    calls = []

    def fake_register(name, port=None, *, preferred=None, type="host", override=False):
        calls.append(name)
        return {"name": name, "port": preferred or 8080, "type": type}

    import agentevolver.port

    monkeypatch.setattr(agentevolver.port.port_manager, "register", fake_register)
    return calls


def test_building_the_manager_claims_no_port(registry_writes):
    SandboxServerManager()
    assert registry_writes == []


def test_the_port_is_claimed_on_first_use(registry_writes):
    manager = SandboxServerManager()
    assert manager.domain.startswith("localhost:")
    assert registry_writes == ["opensandbox"]


def test_the_domain_is_resolved_once(registry_writes, monkeypatch):
    """Resolution is cached; reading it repeatedly must not re-register."""
    import agentevolver.sandbox.process as process

    monkeypatch.setattr(process, "_default_domain", None)
    manager = SandboxServerManager()
    first = manager.domain
    assert manager.domain == first
    assert registry_writes == ["opensandbox"]


def test_an_explicit_domain_never_touches_the_registry(registry_writes):
    """A caller that already knows the daemon's address has nothing to claim."""
    manager = SandboxServerManager(domain="localhost:9999")
    assert manager.domain == "localhost:9999"
    assert registry_writes == []


def test_building_the_environment_manager_claims_no_port(registry_writes, monkeypatch):
    """The roster decides whether a daemon is needed, and it is not read until
    `initialize()` — so the constructor has nothing to go on and must claim nothing."""
    import agentevolver.sandbox.process as process

    monkeypatch.setattr(process, "_default_domain", None)
    from agentevolver.environment.context import EnvironmentContextManager

    EnvironmentContextManager()
    assert registry_writes == []
