"""IDE manager: idle bookkeeping, the instance cap, and what leaks to the client.

The gateway has no ``session.close``, so *time* is what frees these containers —
every path that touches an IDE has to refresh its clock or the reaper takes a
live editor out from under the user. The other load-bearing rule is that the
internal opensandbox upstream URL never reaches the browser; the client is only
ever told a path on its own origin.

Container startup needs a live sandbox and is not exercised here.
"""

import asyncio
import time

import pytest

from agentevolver.ide.server import IdeManagerServer, base_path
from agentevolver.ide.types import IdeInstance


class FakeSandbox:
    """Stands in for a VscodeSandbox handle."""

    def __init__(self, *, port_url=None, destroy_error=None):
        self.destroyed = False
        self.port_calls = []
        self._port_url = port_url
        self._destroy_error = destroy_error

    async def destroy(self):
        if self._destroy_error:
            raise self._destroy_error
        self.destroyed = True

    async def port_url(self, port):
        self.port_calls.append(port)
        if isinstance(self._port_url, Exception):
            raise self._port_url
        return self._port_url


@pytest.fixture
def ide():
    return IdeManagerServer()


def attach(manager, session_id, *, sandbox=None, last_seen=None, **kw):
    instance = IdeInstance(
        session_id=session_id,
        upstream=f"http://internal-{session_id}:3000",
        base_path=base_path(session_id),
        workspace_root=f"/ws/{session_id}",
        sandbox=sandbox if sandbox is not None else FakeSandbox(),
        **kw,
    )
    if last_seen is not None:
        instance.last_seen = last_seen
    manager._instances[session_id] = instance
    return instance


# ------------------------------------------------------------------- paths
def test_the_editor_is_served_under_a_per_session_sub_path():
    """A per-session hostname is resolved by the *browser*, so it only ever
    worked when the browser ran on the server; a sub-path works anywhere."""
    assert base_path("abc123") == "/ide/abc123"


# ------------------------------------------------------------- idle clock
def test_a_proxied_request_refreshes_the_idle_clock(ide):
    instance = attach(ide, "s1", last_seen=0.0)
    assert ide.upstream("s1") == "http://internal-s1:3000"
    assert instance.last_seen > 0.0


def test_a_heartbeat_refreshes_the_idle_clock(ide):
    instance = attach(ide, "s1", last_seen=0.0)
    assert ide.touch("s1") is True
    assert instance.last_seen > 0.0


@pytest.mark.asyncio
async def test_a_port_lookup_also_refreshes_the_idle_clock(ide):
    """A dev server proxied through the container keeps the IDE alive too."""
    instance = attach(ide, "s1", sandbox=FakeSandbox(port_url="http://p:1"), last_seen=0.0)
    await ide.upstream_for_port("s1", 5173)
    assert instance.last_seen > 0.0


def test_lookups_for_an_unknown_session_report_nothing(ide):
    assert ide.upstream("ghost") is None
    assert ide.touch("ghost") is False


@pytest.mark.asyncio
async def test_a_port_lookup_for_an_unknown_session_reports_nothing(ide):
    assert await ide.upstream_for_port("ghost", 5173) is None


# ------------------------------------------------------------ port mapping
@pytest.mark.asyncio
async def test_a_resolved_port_is_cached_for_the_container_s_life(ide):
    """One page load pulls dozens of assets; each must not re-ask opensandbox."""
    sandbox = FakeSandbox(port_url="http://exposed:1234")
    attach(ide, "s1", sandbox=sandbox)
    for _ in range(3):
        assert await ide.upstream_for_port("s1", 5173) == "http://exposed:1234"
    assert sandbox.port_calls == [5173]


@pytest.mark.asyncio
async def test_different_ports_resolve_independently(ide):
    sandbox = FakeSandbox(port_url="http://exposed:1")
    attach(ide, "s1", sandbox=sandbox)
    await ide.upstream_for_port("s1", 5173)
    await ide.upstream_for_port("s1", 8080)
    assert sandbox.port_calls == [5173, 8080]


@pytest.mark.asyncio
async def test_a_port_with_nothing_listening_is_reported_as_absent(ide):
    """Not an error — the user simply has not started anything there yet."""
    attach(ide, "s1", sandbox=FakeSandbox(port_url=RuntimeError("not exposable")))
    assert await ide.upstream_for_port("s1", 9999) is None


@pytest.mark.asyncio
async def test_a_failed_port_lookup_is_not_cached(ide):
    sandbox = FakeSandbox(port_url=RuntimeError("nope"))
    attach(ide, "s1", sandbox=sandbox)
    await ide.upstream_for_port("s1", 9999)
    await ide.upstream_for_port("s1", 9999)
    assert sandbox.port_calls == [9999, 9999]


# --------------------------------------------------------------- teardown
@pytest.mark.asyncio
async def test_stopping_destroys_the_container_and_forgets_it(ide):
    sandbox = FakeSandbox()
    attach(ide, "s1", sandbox=sandbox)
    assert await ide.stop("s1") is True
    assert sandbox.destroyed is True
    assert ide.upstream("s1") is None


@pytest.mark.asyncio
async def test_stopping_an_unknown_session_reports_it_was_not_running(ide):
    assert await ide.stop("ghost") is False


@pytest.mark.asyncio
async def test_a_failing_teardown_still_frees_the_slot(ide):
    """Teardown must never raise into the caller, or one bad container wedges the cap."""
    attach(ide, "s1", sandbox=FakeSandbox(destroy_error=RuntimeError("docker gone")))
    assert await ide.stop("s1") is True
    assert ide._instances == {}


@pytest.mark.asyncio
async def test_stop_all_tears_everything_down_and_cancels_the_reaper(ide):
    sandboxes = [FakeSandbox() for _ in range(3)]
    for n, sandbox in enumerate(sandboxes):
        attach(ide, f"s{n}", sandbox=sandbox)
    ide._reaper = asyncio.create_task(asyncio.sleep(3600))

    await ide.stop_all()
    assert ide._instances == {}
    assert all(s.destroyed for s in sandboxes)
    assert ide._reaper is None


# -------------------------------------------------------------------- cap
@pytest.mark.asyncio
async def test_reaching_the_cap_evicts_the_least_recently_used(ide):
    """Each IDE costs a few hundred MB, so the cap is real, not advisory."""
    now = time.time()
    for n in range(ide.max_instances):
        attach(ide, f"s{n}", last_seen=now - (10 - n))  # s0 is the oldest
    await ide._evict_if_full()
    assert "s0" not in ide._instances
    assert len(ide._instances) == ide.max_instances - 1


@pytest.mark.asyncio
async def test_eviction_keeps_going_until_there_is_room(ide):
    now = time.time()
    for n in range(ide.max_instances + 2):
        attach(ide, f"s{n}", last_seen=now - (100 - n))
    await ide._evict_if_full()
    assert len(ide._instances) == ide.max_instances - 1


@pytest.mark.asyncio
async def test_nothing_is_evicted_below_the_cap(ide):
    attach(ide, "s1")
    await ide._evict_if_full()
    assert "s1" in ide._instances


# ------------------------------------------------------------------ reaper
@pytest.mark.asyncio
async def test_an_idle_ide_is_reaped_and_an_active_one_is_not(ide, monkeypatch):
    monkeypatch.setattr(ide, "reap_interval_seconds", 0.01)
    sandbox = FakeSandbox()
    attach(ide, "idle", sandbox=sandbox, last_seen=time.time() - ide.idle_timeout_seconds - 1)
    attach(ide, "active")

    reaper = asyncio.create_task(ide._reap_loop())
    await asyncio.sleep(0.05)
    reaper.cancel()

    assert "idle" not in ide._instances
    assert "active" in ide._instances
    assert sandbox.destroyed is True


def test_the_container_outlives_the_idle_timeout_by_a_wide_margin(ide):
    """This manager, not opensandbox, must be the one that decides an IDE dies."""
    assert ide.container_timeout_minutes * 60 > ide.idle_timeout_seconds


# ------------------------------------------------------------- credentials
def test_only_credentials_that_are_actually_set_are_forwarded(monkeypatch):
    for name in IdeManagerServer.CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert IdeManagerServer._agent_credentials() == {"ANTHROPIC_API_KEY": "sk-test"}


def test_an_unset_environment_forwards_nothing(monkeypatch):
    for name in IdeManagerServer.CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    assert IdeManagerServer._agent_credentials() == {}


def test_the_callback_free_login_token_is_among_the_forwarded_vars():
    """A browser OAuth redirect cannot complete inside the container; the
    long-lived token is the only path that works."""
    assert "CLAUDE_CODE_OAUTH_TOKEN" in IdeManagerServer.CREDENTIAL_VARS


# ------------------------------------------------------------------ status
def test_status_never_leaks_the_internal_upstream(ide):
    """The client gets a path on its own origin, so it works over any tunnel."""
    attach(ide, "s1")
    status = ide.status("s1")
    assert status["running"] is True
    assert status["path"] == "/ide/s1/"
    assert "internal-s1" not in repr(status)
    assert "upstream" not in status


def test_status_for_an_unknown_session_says_not_running(ide):
    assert ide.status("ghost") == {"session_id": "ghost", "running": False}


def test_status_reports_how_long_it_has_been_idle(ide):
    attach(ide, "s1", last_seen=time.time() - 30)
    assert ide.status("s1")["idle_seconds"] == pytest.approx(30, abs=1)


def test_the_live_handle_stays_out_of_serialisation(ide):
    instance = attach(ide, "s1")
    dumped = instance.model_dump()
    assert "sandbox" not in dumped
    assert "port_upstreams" not in dumped
