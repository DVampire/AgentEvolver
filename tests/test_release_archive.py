"""An older release has to still exist somewhere before it can be served again.

`/s/<site>--r<n>/` was only ever an assertion. Nothing wrote `release_number`, so it sat
at 0 and every pinned lookup failed the comparison — but the deeper problem was that even
a correct lookup had nothing to answer with. The agent edits its workspace in place, the
staging tree is wiped on each redeploy, and the registry keeps one record per site, so the
moment release n+1 landed, release n's bytes were gone from the machine. One live site
published six releases and kept the source of exactly one.

So a release is archived when it is published, and brought back on demand when someone
addresses it.
"""

import os

import pytest

from agentevolver.deploy import deployment_manager
from agentevolver.deploy.types import DeployRequest, SiteRecord, SiteStatus


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """The real manager, with its registry (and so its archive) under tmp_path."""
    monkeypatch.setattr(
        deployment_manager, "_registry_path", str(tmp_path / "deploy" / "registry.json")
    )
    deployment_manager._sites.pop("echo-ark", None)
    deployment_manager._release_seen.clear()
    try:
        yield deployment_manager
    finally:
        deployment_manager._sites.pop("echo-ark", None)
        deployment_manager._sites.pop("echo-ark--r1", None)
        deployment_manager._release_seen.clear()


def _source(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(text)
    return str(root)


def test_publishing_archives_the_source(manager, tmp_path):
    """The bytes survive the next release overwriting the workspace."""
    src = _source(tmp_path / "ws" / "site", "<h1>release one</h1>")
    manager._archive_release("echo-ark", 1, src)

    # The agent edits in place, exactly as it does in a run.
    (tmp_path / "ws" / "site" / "index.html").write_text("<h1>release two</h1>")

    archived = os.path.join(manager._release_dir("echo-ark", 1), "index.html")
    assert open(archived).read() == "<h1>release one</h1>"


def test_each_release_gets_its_own_directory(manager, tmp_path):
    """Archiving r2 must not disturb r1 — otherwise there is still only ever one."""
    manager._archive_release("echo-ark", 1, _source(tmp_path / "a", "one"))
    manager._archive_release("echo-ark", 2, _source(tmp_path / "b", "two"))
    for release, expected in ((1, "one"), (2, "two")):
        path = os.path.join(manager._release_dir("echo-ark", release), "index.html")
        assert open(path).read() == expected


def test_a_release_number_advances_only_when_the_source_changes(manager):
    """A redeploy of identical bytes is a restart. Numbering it would point `--r<n>` at
    something the reader was never shown."""
    assert manager._split_release("echo-ark--r3") == ("echo-ark", 3)
    assert manager._split_release("echo-ark") is None
    assert manager._split_release("echo-ark--rX") is None


def test_the_current_release_resolves_without_starting_anything(manager):
    """`--r<n>` naming the release already serving is the live site, not an archive."""
    manager._sites["echo-ark"] = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    assert manager.resolve_port("echo-ark--r3") == 8899


def test_an_older_release_is_not_confused_with_the_current_one(manager):
    """The old behaviour: a pin for a release that is not current resolved to nothing.
    It must still not resolve to the *current* port, which would answer a question about
    release 1 with release 3's bytes."""
    manager._sites["echo-ark"] = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    assert manager.resolve_port("echo-ark--r1") is None


@pytest.mark.asyncio
async def test_asking_for_an_unarchived_release_returns_nothing(manager):
    """No archive, no answer — never a fabricated port."""
    manager._sites["echo-ark"] = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
    )
    assert await manager.ensure_release("echo-ark--r1") is None


@pytest.mark.asyncio
async def test_an_archived_release_is_started_on_demand(manager, tmp_path, monkeypatch):
    """The point of the whole feature: r1's own bytes come back, on their own port."""
    manager._sites["echo-ark"] = SiteRecord(
        site_id="echo-ark", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:8899", port=8899, release_number=3,
        request={"site_id": "echo-ark", "runtime": "static"},
    )
    manager._archive_release("echo-ark", 1, _source(tmp_path / "ws", "<h1>one</h1>"))

    started = {}

    async def _deploy(request: DeployRequest):
        started["site_id"] = request.site_id
        started["source_dir"] = request.source_dir
        started["backend"] = request.backend
        manager._sites[request.site_id] = SiteRecord(
            site_id=request.site_id, runtime="static", status=SiteStatus.RUNNING,
            url="http://localhost:9001", port=9001, release_number=1,
        )
        return manager._sites[request.site_id]

    monkeypatch.setattr(manager, "deploy", _deploy)
    port = await manager.ensure_release("echo-ark--r1")

    assert port == 9001
    assert started["site_id"] == "echo-ark--r1"
    assert started["backend"] == "host"
    assert open(os.path.join(started["source_dir"], "index.html")).read() == "<h1>one</h1>"


@pytest.mark.asyncio
async def test_a_pinned_release_does_not_archive_itself(manager, tmp_path, monkeypatch):
    """A `<site>--r<n>` site is an archive being served, not a site that publishes.

    Archiving it again copied an archive into an archive on every lazy start — the same
    bytes duplicated under a nested releases/ directory each time anyone opened an older
    release — and numbered it by this site's own deploy count instead of the release it
    is pinned to."""
    archived = []
    monkeypatch.setattr(
        manager, "_archive_release",
        lambda site_id, release, source: archived.append((site_id, release)) or "",
    )
    src = _source(tmp_path / "ws", "<h1>three</h1>")

    # Let the real bookkeeping run, then stop at the sandbox so no process is started.
    from agentevolver.deploy import server as server_module

    async def _no_sandbox(*a, **k):
        raise RuntimeError("stop after the bookkeeping")

    monkeypatch.setattr(server_module.sandbox_manager, "acquire", _no_sandbox)
    # The deploy fails at the sandbox; the bookkeeping under test already ran.
    await manager.deploy(DeployRequest(site_id="echo-ark--r3", runtime="static", source_dir=src))

    assert archived == []
    assert manager._sites["echo-ark--r3"].release_number == 3


@pytest.mark.asyncio
async def test_an_ordinary_site_still_archives(manager, tmp_path, monkeypatch):
    """The guard above must not switch archiving off for sites that do publish."""
    archived = []
    monkeypatch.setattr(
        manager, "_archive_release",
        lambda site_id, release, source: archived.append((site_id, release)) or "",
    )
    src = _source(tmp_path / "ws", "<h1>one</h1>")

    from agentevolver.deploy import server as server_module

    async def _no_sandbox(*a, **k):
        raise RuntimeError("stop after the bookkeeping")

    monkeypatch.setattr(server_module.sandbox_manager, "acquire", _no_sandbox)
    await manager.deploy(DeployRequest(site_id="echo-ark", runtime="static", source_dir=src))

    assert archived == [("echo-ark", 1)]


@pytest.mark.asyncio
async def test_stopping_a_site_stops_the_releases_opened_beside_it(manager, monkeypatch):
    """An older release exists to be read beside the site it is a version of.

    Left running it outlives that site, holding a port and answering for something that
    is gone — and nobody owns it: the caller never deployed it, it appeared because a
    visitor opened one."""
    for name, release in (("echo-ark", 3), ("echo-ark--r1", 1)):
        manager._sites[name] = SiteRecord(
            site_id=name, runtime="static", status=SiteStatus.RUNNING,
            url="http://localhost:8899", port=8899, release_number=release,
        )
    manager._release_seen["echo-ark--r1"] = 0.0

    from agentevolver.deploy import server as server_module

    async def _released(*a, **k):
        return True

    monkeypatch.setattr(server_module.sandbox_manager, "release", _released)
    await manager.stop_site("echo-ark")

    assert manager._sites["echo-ark--r1"].status is SiteStatus.STOPPED


@pytest.mark.asyncio
async def test_a_release_left_by_a_previous_process_still_gets_reaped(manager):
    """The registry survives a restart; the last-seen times do not. A pinned release with
    no sighting must not become a port held forever — nor be cut off mid-read, so it gets
    a first sighting now and a full idle window from there."""
    manager._sites["echo-ark--r1"] = SiteRecord(
        site_id="echo-ark--r1", runtime="static", status=SiteStatus.RUNNING,
        url="http://localhost:9001", port=9001, release_number=1,
    )
    assert "echo-ark--r1" not in manager._release_seen

    await manager._reap_idle_releases()

    assert manager._sites["echo-ark--r1"].status is SiteStatus.RUNNING
    assert "echo-ark--r1" in manager._release_seen
