"""What a deploy resolves to before any container is involved.

The manager never knows React from FastAPI. A profile turns a ``DeployRequest`` into a
``DeploymentSpec``, and the manager only executes it: upload, build, start, expose,
health-check. Everything decided ahead of that lives here — and every mistake in it
surfaces the same unhelpful way, as "service did not become healthy within 120s". A
start command pinned to ``127.0.0.1`` is unreachable through ``expose_port``; a port in
the record the start command does not actually serve binds a URL to nothing; a dropped
override runs the profile's default instead of what the caller asked for. None of those
say so in the failure.

The registry is the other half. It is the only trace of a site that survives a process
restart, so a record that fails to load is a live server nobody can list, stop, or
redeploy — the container keeps running and the framework has forgotten it exists.

Nothing here starts a container: the acquire → upload → build → start path needs a live
sandbox and is not exercised.
"""

import os

import pytest

from agentevolver.deploy.server import DeploymentManagerServer
from agentevolver.deploy.types import (
    DeploymentSpec,
    DeployRequest,
    HealthCheck,
    ResourceSpec,
    SiteRecord,
    SiteStatus,
)


@pytest.fixture
def manager(tmp_path):
    """A manager whose registry is a throwaway file, with initialization skipped.

    ``_initialized`` is set by hand because the real ``initialize()`` reconciles every
    recorded site over HTTP; these tests want the resolution logic, not the network.
    """
    import agentevolver.deploy.default  # noqa: F401 — registers the built-in profiles

    server = DeploymentManagerServer()
    server._registry_path = str(tmp_path / "sites.json")
    server._initialized = True
    return server


# --------------------------------------------------------------------------- #
# What a profile resolves to
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_built_in_profiles_are_discoverable(manager):
    """These four names are what an agent picks from; a profile that fails to register
    is simply absent, and the deploy fails as "no such profile" long after the mistake."""
    profiles = await manager.list_profiles()
    assert {"static", "node", "python", "custom"} <= set(profiles)


def test_an_unknown_profile_names_the_ones_that_exist(manager):
    """The caller is usually a model guessing at a runtime string.

    "No deploy profile 'nope'" on its own invites another guess; listing the available
    names turns the second attempt into a correct one.
    """
    with pytest.raises(ValueError, match="No deploy profile 'nope'"):
        manager._profile("nope")


def test_a_static_site_needs_no_build_step(manager):
    """Pre-built files have nothing to compile, and an empty build list is the way that
    is said — a profile that invented a step here would fail on every plain HTML site."""
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime="static"))
    assert spec.build == []
    assert spec.port == 8000


@pytest.mark.parametrize("runtime", ["static", "node", "python"])
def test_no_profile_pins_its_server_to_loopback(manager, runtime):
    """A server on 127.0.0.1 is unreachable through ``expose_port`` — silent breakage.

    The site is built, the process starts, the port is bound, and the URL answers
    nothing. Profiles either say ``0.0.0.0`` outright or use a server that defaults to
    every interface; what none of them may do is pin loopback.
    """
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime=runtime))
    assert "127.0.0.1" not in spec.start
    assert "localhost" not in spec.start


@pytest.mark.parametrize("runtime", ["static", "node", "python"])
def test_every_profile_serves_the_port_it_reports(manager, runtime):
    """The recorded port is what gets exposed and becomes the site URL.

    If the start command still carries the profile default while the record carries the
    requested one, the URL points at a port nothing is listening on. ``$PORT`` counts:
    the manager exports the chosen port under that name for exactly this reason.
    """
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime=runtime, port=7654))
    assert "7654" in spec.start or "$PORT" in spec.start


def test_the_custom_profile_refuses_to_guess_a_start_command(manager):
    """``custom`` exists because nothing generic fits, so there is nothing to fall back
    on. Inventing a default here would start the wrong process and report it healthy the
    moment anything answered the port."""
    with pytest.raises(ValueError, match="requires overrides.start"):
        manager._resolve_spec(DeployRequest(site_id="s", runtime="custom"))


def test_the_custom_profile_takes_everything_from_overrides(manager):
    """The escape hatch has to be a complete one: image, build and start all come from
    the caller, or a Go binary would still be built by a Python profile's defaults."""
    spec = manager._resolve_spec(
        DeployRequest(
            site_id="s",
            runtime="custom",
            overrides={"image": "golang:1.22", "build": ["go build ."], "start": "./app"},
        )
    )
    assert spec.image == "golang:1.22"
    assert spec.build == ["go build ."]
    assert spec.start == "./app"


# --------------------------------------------------------------------------- #
# Which value wins when the request and the profile disagree
# --------------------------------------------------------------------------- #
def test_an_explicit_port_beats_the_profile_default(manager):
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime="static", port=9999))
    assert spec.port == 9999


def test_request_env_is_merged_over_the_profile_env(manager):
    """Merged, not replaced: the profile's own variables have to survive alongside the
    caller's, or a request that passes one key would strip the rest of the environment."""
    spec = manager._resolve_spec(
        DeployRequest(
            site_id="s",
            runtime="static",
            env={"API_KEY": "secret"},
        )
    )
    assert spec.env["API_KEY"] == "secret"


@pytest.mark.parametrize(
    "field, value",
    [
        ("image", "alpine:3.20"),
        ("workspace_root", "/srv"),
        ("build", ["make"]),
        ("start", "./run.sh"),
        ("timeout_minutes", 5),
    ],
)
def test_each_overridable_field_wins_over_the_profile(manager, field, value):
    """One case per field, because the overlay is a hand-written list of field names.

    A field left out of that list is not rejected — it is accepted and ignored, and the
    site runs the profile's version of whatever the caller thought they had changed.
    """
    spec = manager._resolve_spec(
        DeployRequest(
            site_id="s",
            runtime="static",
            overrides={field: value},
        )
    )
    assert getattr(spec, field) == value


def test_a_null_override_does_not_erase_the_profile_value(manager):
    """``None`` means "not specified", not "clear it".

    Callers routinely build overrides by dumping a model, which fills every unset field
    with ``None``. Treating those as real values would blank the image, the start
    command and the workspace in one go.
    """
    spec = manager._resolve_spec(
        DeployRequest(
            site_id="s",
            runtime="static",
            overrides={"image": None},
        )
    )
    assert spec.image == "python:3.11-slim"


def test_a_health_override_is_accepted_as_a_plain_dict(manager):
    """Overrides arrive as JSON, so the health check arrives as a dict rather than a
    ``HealthCheck``. Storing the dict unconverted would break the probe loop later, at
    the point where it reads ``health.type``."""
    spec = manager._resolve_spec(
        DeployRequest(
            site_id="s",
            runtime="static",
            overrides={"health": {"type": "command", "command": "true"}},
        )
    )
    assert isinstance(spec.health, HealthCheck)
    assert spec.health.type == "command"
    assert spec.health.command == "true"


# --------------------------------------------------------------------------- #
# Choosing between a container and the bare host
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "choice, expected",
    [
        ("host", "host"),
        ("local", "host"),
        ("sandbox", "opensandbox"),
        ("opensandbox", "opensandbox"),
        ("docker", "opensandbox"),
        ("HOST", "host"),  # case-insensitive
        ("  host  ", "host"),  # and whitespace-tolerant
    ],
)
def test_every_spelling_of_the_backend_choice_lands_on_the_same_backend(
    manager, monkeypatch, choice, expected
):
    """The value is typed into an env var by a person, so aliases and stray whitespace
    are the normal case rather than the exotic one.

    An unrecognised spelling does not raise — it falls through to auto-detection, so a
    dropped alias means the run quietly uses a backend nobody asked for. ``host`` in
    particular gives up container isolation, which is not something to arrive at by a
    typo in ``DEPLOY_BACKEND``.
    """
    monkeypatch.setenv("DEPLOY_BACKEND", choice)
    assert manager._backend_kind() == expected


def test_auto_falls_back_to_the_host_when_no_container_runtime_is_reachable(manager, monkeypatch):
    """A deploy still has to work on a machine without Docker.

    The host backend gives up isolation, which is why it is never the default — but
    refusing to deploy at all would make the whole subsystem unusable on the machines
    where it is most often demonstrated.
    """
    monkeypatch.setenv("DEPLOY_BACKEND", "auto")
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: False))
    assert manager._backend_kind() == "host"


def test_auto_prefers_the_container_when_one_is_reachable(manager, monkeypatch):
    """With no env var set at all, the isolated backend is what a site gets."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    assert manager._backend_kind() == "opensandbox"


def test_a_remote_docker_host_counts_as_a_container_runtime(manager, monkeypatch):
    """Probing only for a local ``/var/run/docker.sock`` would send every deploy on a
    remote-daemon machine to the unisolated host backend."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2375")
    assert manager._container_runtime_available() is True


def test_a_host_site_gets_its_own_directory_under_the_registry(manager, tmp_path):
    """Host-backend sites share one filesystem, so their source trees must not share a
    directory — two sites unpacking into the same place would overwrite each other."""
    site_dir = manager._host_site_dir("my-site")
    assert site_dir.startswith(str(tmp_path))
    assert site_dir.endswith(os.path.join("sites", "my-site", "app"))


# --------------------------------------------------------------------------- #
# The lightweight path: inline content instead of a source tree
# --------------------------------------------------------------------------- #
def test_inline_content_defaults_to_the_host_backend(manager, monkeypatch):
    """A page shipped in the request is the lightweight case, and spinning an isolated
    container per page defeats the point. With nothing forcing a backend, inline
    content/files deploy on the host — instant, and reachable at a real localhost port."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    assert manager._backend_kind(DeployRequest(site_id="s", content="<h1>hi</h1>")) == "host"
    assert manager._backend_kind(DeployRequest(site_id="s", files={"index.html": "x"})) == "host"


def test_a_source_dir_still_defaults_to_a_container(manager, monkeypatch):
    """The inline-⇒-host rule must not leak onto real project trees: a source_dir is a
    heavier deploy that still wants isolation when Docker is around."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    assert manager._backend_kind(DeployRequest(site_id="s", source_dir="/tmp/x")) == "opensandbox"


def test_an_explicit_backend_beats_the_inline_host_default(manager, monkeypatch):
    """Lightweight-by-default, not lightweight-only: a caller who wants a page isolated in
    a container says so with ``backend``, and that choice wins over the inline default."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    assert (
        manager._backend_kind(
            DeployRequest(site_id="s", content="<h1>hi</h1>", backend="opensandbox")
        )
        == "opensandbox"
    )


def test_the_request_backend_beats_the_env(manager, monkeypatch):
    """A per-deploy choice is more specific than a machine-wide env default, so it wins."""
    monkeypatch.setenv("DEPLOY_BACKEND", "host")
    assert manager._backend_kind(DeployRequest(site_id="s", backend="opensandbox")) == "opensandbox"


def test_inline_content_is_materialized_as_the_named_file(manager):
    """``content`` becomes a real file the normal upload path can serve; the default name
    is ``index.html`` so a bare page is browsable at the site root."""
    req = DeployRequest(site_id="s", content="<h1>hi</h1>")
    staging = manager._materialize_inline(req)
    with open(os.path.join(staging, "index.html")) as fh:
        assert fh.read() == "<h1>hi</h1>"


def test_inline_files_are_materialized_preserving_relative_paths(manager):
    """A multi-file artifact keeps its layout — ``app.js`` next to ``index.html`` — or the
    page's own relative script/style references break once served."""
    req = DeployRequest(site_id="s", files={"index.html": "<b>a</b>", "js/app.js": "x=1"})
    staging = manager._materialize_inline(req)
    assert os.path.isfile(os.path.join(staging, "index.html"))
    assert os.path.isfile(os.path.join(staging, "js", "app.js"))


def test_inline_content_fills_in_a_missing_file_in_the_files_map(manager):
    """``content`` + ``files`` together is a page plus its assets: content supplies the
    entry file only when the map didn't already, so an explicit index is never clobbered."""
    req = DeployRequest(site_id="s", content="<h1>entry</h1>", files={"style.css": "b{}"})
    staging = manager._materialize_inline(req)
    with open(os.path.join(staging, "index.html")) as fh:
        assert fh.read() == "<h1>entry</h1>"
    assert os.path.isfile(os.path.join(staging, "style.css"))


def test_a_redeploy_does_not_serve_stale_inline_files(manager):
    """The staging dir is reused per site_id, so a file dropped from a redeploy must not
    linger from the previous materialization and keep being served."""
    manager._materialize_inline(DeployRequest(site_id="s", files={"old.html": "gone"}))
    staging = manager._materialize_inline(DeployRequest(site_id="s", files={"new.html": "here"}))
    assert not os.path.exists(os.path.join(staging, "old.html"))
    assert os.path.isfile(os.path.join(staging, "new.html"))


def test_inline_file_paths_cannot_escape_the_staging_dir(manager):
    """The relpath keys come from a model; a ``../`` in one would write outside the site's
    own directory — over another site's files or anything else the process can reach."""
    with pytest.raises(ValueError, match="unsafe inline file path"):
        manager._materialize_inline(DeployRequest(site_id="s", files={"../evil.sh": "rm -rf /"}))


def test_inline_with_no_content_is_rejected(manager):
    """An inline deploy that carries neither content nor files has nothing to serve, and
    an empty site published as "running" is worse than a clear error."""
    with pytest.raises(ValueError, match="non-empty content or files"):
        manager._materialize_inline(DeployRequest(site_id="s", files={}))


# --------------------------------------------------------------------------- #
# The registry as the only thing that outlives the process
# --------------------------------------------------------------------------- #
def test_the_registry_survives_a_restart(manager, tmp_path):
    """Sandbox handles die with the process; the JSON file is what is left.

    A second manager reading the same path has to see the URL and the runtime, because
    those are what ``redeploy`` and ``stop_site`` work from. Without them a running site
    is unreachable through the framework that started it.
    """
    manager._sites["a"] = SiteRecord(site_id="a", runtime="static", url="http://x", port=8000)
    manager._save()

    reloaded = DeploymentManagerServer()
    reloaded._registry_path = str(tmp_path / "sites.json")
    reloaded._load()
    assert reloaded._sites["a"].url == "http://x"
    assert reloaded._sites["a"].runtime == "static"


def test_a_corrupt_registry_degrades_to_empty_rather_than_crashing(manager, tmp_path):
    """A bad registry must not stop the framework from starting.

    The file is rewritten on every deploy, so a process killed mid-write leaves exactly
    this. Raising here would make one truncated JSON file fatal to the whole framework,
    for a subsystem most runs never touch. The stale in-memory map is dropped too: half
    a picture is harder to reason about than none.
    """
    (tmp_path / "sites.json").write_text("{ not json")
    manager._sites = {"stale": SiteRecord(site_id="stale", runtime="static")}
    manager._load()
    assert manager._sites == {}


def test_saving_without_a_registry_path_is_a_no_op():
    """An uninitialized manager has no path yet, and ``_save`` is called from paths that
    do not know whether ``initialize()`` has run."""
    DeploymentManagerServer()._save()  # must not raise


# --------------------------------------------------------------------------- #
# Recovering sites after a restart
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_site_still_answering_is_reattached_as_running(manager, monkeypatch):
    """The handle is gone but the server is not: a detached host process or a container
    outlives the framework, so a restart must reattach rather than declare it dead."""
    manager._sites["a"] = SiteRecord(
        site_id="a", runtime="static", url="http://x", status=SiteStatus.RUNNING
    )
    monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _true()))
    await manager._reconcile_on_start()
    assert manager._sites["a"].status is SiteStatus.RUNNING


@pytest.mark.asyncio
async def test_a_site_that_no_longer_answers_becomes_redeployable(manager, monkeypatch):
    """The handle dies with the process; the record must say so, not lie.

    ``DETACHED`` is the state ``redeploy`` acts on. Leaving the record as ``RUNNING``
    would show a working site in every listing and give no way to bring it back.
    """
    manager._sites["a"] = SiteRecord(
        site_id="a", runtime="static", url="http://x", status=SiteStatus.RUNNING
    )
    monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _false()))
    await manager._reconcile_on_start()
    assert manager._sites["a"].status is SiteStatus.DETACHED


@pytest.mark.asyncio
async def test_deliberately_stopped_sites_are_left_alone(manager, monkeypatch):
    """``STOPPED`` and ``FAILED`` are decisions, not observations.

    Both are unreachable, so a reconciler that only probes would relabel them
    ``DETACHED`` and present a site somebody stopped on purpose as one waiting to be
    brought back.
    """
    for status in (SiteStatus.STOPPED, SiteStatus.FAILED):
        manager._sites = {"a": SiteRecord(site_id="a", runtime="static", status=status)}
        monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _false()))
        await manager._reconcile_on_start()
        assert manager._sites["a"].status is status


@pytest.mark.asyncio
async def test_a_site_without_a_url_is_never_considered_reachable(manager):
    """A record that never got as far as being exposed has nothing to probe; an empty
    string must not reach the HTTP client and come back as something other than False."""
    assert await DeploymentManagerServer._url_reachable(None) is False
    assert await DeploymentManagerServer._url_reachable("") is False


async def _true():
    return True


async def _false():
    return False


# --------------------------------------------------------------------------- #
# Defaults the types carry on their own
# --------------------------------------------------------------------------- #
def test_a_fresh_site_starts_pending():
    assert SiteRecord(site_id="a", runtime="static").status is SiteStatus.PENDING


def test_a_site_record_keeps_the_request_so_it_can_be_redeployed():
    """``redeploy`` rebuilds from the stored request and nothing else.

    A record that dropped it — or stored it in a shape ``DeployRequest`` cannot read
    back — leaves the site permanently unrecoverable after a restart.
    """
    request = DeployRequest(site_id="a", runtime="static", port=8080)
    record = SiteRecord(site_id="a", runtime="static", request=request.model_dump())
    assert DeployRequest(**record.request).port == 8080


def test_health_checks_default_to_probing_the_root_over_http():
    """The intervals matter as much as the type: a zero or negative interval turns the
    readiness loop into a busy wait against a container that is still building."""
    check = HealthCheck()
    assert (check.type, check.path) == ("http", "/")
    assert check.timeout_s > 0 and check.interval_s > 0


def test_gpu_is_off_by_default():
    """GPU passthrough is a placeholder the sandbox backend does not implement, so any
    non-zero default would request hardware nothing can supply."""
    assert ResourceSpec().gpu == 0


def test_a_spec_requires_the_fields_the_manager_executes():
    """``start`` and ``port`` have no sensible default — the manager runs one and exposes
    the other. A spec that validated without them would fail deep inside the deploy, with
    the container already acquired and the source already uploaded."""
    with pytest.raises(Exception):
        DeploymentSpec(runtime="static", image="x")  # no start, no port


def test_a_deploy_request_defaults_to_the_static_profile():
    assert DeployRequest(site_id="a").runtime == "static"
