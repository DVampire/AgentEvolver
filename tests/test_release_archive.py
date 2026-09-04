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
