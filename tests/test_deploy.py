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

import inspect
import os
from types import SimpleNamespace

import pytest

from agentevolver.agent.types import AgentContext
from agentevolver.deploy.server import DeploymentManagerServer
from agentevolver.deploy.types import (
    DeploymentSpec,
    DeployRequest,
    HealthCheck,
    ResourceSpec,
    SiteRecord,
    SiteStatus,
)
from agentevolver.dynamic import dynamic_manager
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.default.deployment.deploy import DeployTool, deployment_manager
from agentevolver.tool.default.adoption import AdoptionTool
from agentevolver.tool.types import ToolContext


def test_deploy_tool_native_schema_exposes_every_action_argument():
    """Native tool calls must not depend on permissive ``**kwargs`` forwarding.

    A kwargs-only payload looks usable to Python but disappears from the inferred JSON
    schema. Strict provider routes then retain only ``action`` and make deploy/redeploy
    impossible after a context compaction rebuilds the native tool list.
    """
    parameters = dynamic_manager.get_parameters(DeployTool)

    assert set(parameters["properties"]) == {
        "action",
        "site_id",
        "runtime",
        "source_dir",
        "git_url",
        "content",
        "files",
        "filename",
        "backend",
        "port",
        "env",
        "overrides",
    }
    assert parameters["properties"]["action"]["enum"] == [
        "preview",
        "deploy",
        "list",
        "get",
        "stop",
        "redeploy",
    ]
    assert parameters["additionalProperties"] is False


def test_deploy_tool_accepts_runtime_context_without_exposing_it_to_model():
    """The manager injects ``ctx`` into every tool invocation.

    Runtime-only kwargs must remain accepted by Python while staying absent from the
    strict provider schema.
    """
    signature = inspect.signature(DeployTool.__call__)
    assert any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert "ctx" not in dynamic_manager.get_parameters(DeployTool)["properties"]


def test_adoption_tool_exposes_lifecycle_arguments_to_strict_providers():
    parameters = dynamic_manager.get_parameters(AdoptionTool)

    assert {
        "action",
        "module",
        "name",
        "version",
        "version_a",
        "version_b",
        "success",
        "quality_score",
        "run_id",
        "case_id",
        "token_cost",
        "elapsed_ms",
        "notes",
        "release_number",
        "decision",
        "evidence",
        "evaluation",
    } == set(parameters["properties"])
    assert "record_decision" in parameters["properties"]["action"]["enum"]
    assert parameters["additionalProperties"] is False


@pytest.mark.asyncio
async def test_deploy_tool_list_accepts_manager_injected_context(monkeypatch, tmp_path):
    async def no_sites():
        return []

    monkeypatch.setattr(deployment_manager, "list_sites", no_sites)
    manager = ToolContextManager(base_dir=str(tmp_path))
    await manager.register(DeployTool, version="test")
    response = await manager(
        name="deploy_tool",
        input={"action": "list"},
        ctx=ToolContext(id="deploy-test"),
    )

    assert response.success is True
    assert response.message == "No deployed sites."


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
    """``auto`` still means the isolated backend wherever a container runtime answers."""
    monkeypatch.setenv("DEPLOY_BACKEND", "auto")
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


def test_a_source_dir_deploys_on_the_host(manager, monkeypatch):
    """A source_dir is a directory this agent just wrote in its own workspace, and the
    agent is already free to run it through bash. A container cannot isolate the machine
    from code that is running unsandboxed beside it, so the isolation would be nominal
    while the costs are real — an opaque proxy URL instead of a localhost port, a build per
    deploy, and a filesystem that resets under whatever the server has written."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    assert manager._backend_kind(DeployRequest(site_id="s", source_dir="/tmp/x")) == "host"


def test_a_git_url_still_defaults_to_a_container(manager, monkeypatch):
    """Foreign code arriving over the network is the case where isolation is earned."""
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    req = DeployRequest(site_id="s", git_url="https://example.com/app.git")
    assert manager._backend_kind(req) == "opensandbox"


def test_a_site_keeps_the_backend_it_is_already_running_on(manager, monkeypatch):
    """``site_id`` is a stable identity, so a redeploy must not move the site.

    One live site shipped six releases split across two substrates because a single
    optional argument stopped being passed: the URL changed shape under everyone already
    holding it, and each move discarded what the server had written since."""
    from agentevolver.deploy.types import SiteRecord

    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    born_in_a_container = SiteRecord(site_id="s", runtime="python", url="http://x", backend="opensandbox")
    req = DeployRequest(site_id="s", source_dir="/tmp/x")
    assert manager._backend_kind(req, born_in_a_container) == "opensandbox"


def test_an_explicit_backend_moves_a_site(manager, monkeypatch):
    """Keeping the substrate is a default, not a cage: saying so still moves the site."""
    from agentevolver.deploy.types import SiteRecord

    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    born_in_a_container = SiteRecord(site_id="s", runtime="python", url="http://x", backend="opensandbox")
    req = DeployRequest(site_id="s", source_dir="/tmp/x", backend="host")
    assert manager._backend_kind(req, born_in_a_container) == "host"


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


def test_source_revision_changes_only_when_authored_source_changes(manager, tmp_path):
    source = tmp_path / "site"
    source.mkdir()
    (source / "index.html").write_text("first", encoding="utf-8")
    request = DeployRequest(site_id="site", source_dir=str(source))

    first = manager._source_revision(request)
    (source / "dist").mkdir()
    (source / "dist" / "generated.js").write_text("ignored", encoding="utf-8")
    assert manager._source_revision(request) == first

    (source / "index.html").write_text("second", encoding="utf-8")
    assert manager._source_revision(request) != first


@pytest.mark.asyncio
async def test_release_publish_receipt_updates_the_parent_context_in_place(monkeypatch):
    from agentevolver.runtime import kernel

    async def publish_scoped(topic, event_type, payload=None, **kwargs):
        assert topic == "deployment.ready"
        assert payload["url"] == "http://site.test"
        return 4, "root::deployment.ready", SimpleNamespace(id="event-1")

    monkeypatch.setattr(kernel, "publish_scoped", publish_scoped)
    parent = AgentContext(
        id="root",
        extra={
            "website_runtime_contract": {"subscriber_job_ids": ["a", "b", "c", "d"]},
            "deployment_release_history": [],
        },
    )
    tool_ctx = ToolContext.from_context(parent)
    record = SiteRecord(
        site_id="site",
        runtime="static",
        status=SiteStatus.RUNNING,
        url="http://site.test",
        source_revision="revision-1",
        updated_at="now",
    )

    receipt = await DeployTool._publish_ready(record, action="deploy", ctx=tool_ctx)

    assert receipt["fanout"] == 4
    assert receipt["release_number"] == 1
    assert parent.extra["deployment_release_history"] == [receipt]


@pytest.mark.asyncio
async def test_generic_deploy_context_does_not_publish_a_website_event(monkeypatch):
    from agentevolver.runtime import kernel

    called = False

    async def publish_scoped(*_args, **_kwargs):
        nonlocal called
        called = True
        return 0, "root::deployment.ready", SimpleNamespace(id="event-1")

    monkeypatch.setattr(kernel, "publish_scoped", publish_scoped)
    record = SiteRecord(
        site_id="site",
        runtime="static",
        status=SiteStatus.RUNNING,
    )

    assert (
        await DeployTool._publish_ready(
            record,
            action="deploy",
            ctx=ToolContext(id="ordinary"),
        )
        == {}
    )
    assert called is False


def test_website_release_must_match_the_latest_preview_revision():
    contract = {
        "latest_preview": {
            "site_id": "echo",
            "source_revision": "revision-2",
        }
    }
    ctx = SimpleNamespace(extra={"website_runtime_contract": contract})

    assert DeployTool._preview_blocker(ctx, "echo", "revision-2") == ""
    assert "changed after preview" in DeployTool._preview_blocker(
        ctx,
        "echo",
        "revision-3",
    )
    assert "belongs to site" in DeployTool._preview_blocker(
        ctx,
        "other",
        "revision-2",
    )


def test_deploy_reports_a_remote_url_for_a_loopback_site(monkeypatch):
    monkeypatch.setenv("DEPLOY_PUBLIC_HOST", "10.20.30.40")
    record = SiteRecord(
        site_id="site",
        runtime="static",
        status=SiteStatus.RUNNING,
        url="http://localhost:8123/path",
    )

    assert DeployTool._access_urls(record) == {
        "internal_url": "http://localhost:8123/path",
        "public_url": "http://10.20.30.40:8123/path",
    }


# --------------------------------------------------------------------------- #
# The registry as the only thing that outlives the process
# --------------------------------------------------------------------------- #
def test_the_registry_survives_a_restart(manager, tmp_path):
    """Sandbox handles die with the process; the JSON file is what is left.

    A second manager reading the same path has to see the URL and the runtime, because
    those are what ``redeploy`` and ``stop_site`` work from. Without them a running site
    is unreachable through the framework that started it.
    """
    manager._sites["a"] = SiteRecord(
        site_id="a",
        runtime="static",
        url="http://x",
        port=8000,
        resource_id="123:456:123",
    )
    manager._save()

    reloaded = DeploymentManagerServer()
    reloaded._registry_path = str(tmp_path / "sites.json")
    reloaded._load()
    assert reloaded._sites["a"].url == "http://x"
    assert reloaded._sites["a"].runtime == "static"
    assert reloaded._sites["a"].resource_id == "123:456:123"


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


@pytest.mark.asyncio
async def test_stop_uses_the_persisted_resource_identity_after_handle_loss(
    manager,
    monkeypatch,
):
    manager._sites["a"] = SiteRecord(
        site_id="a",
        runtime="static",
        backend="host",
        status=SiteStatus.RUNNING,
        url="http://example.test",
        resource_id="123:456:123",
    )
    captured = {}

    async def release(type, *, reuse_key=None, resource_id=None):
        captured.update(type=type, reuse_key=reuse_key, resource_id=resource_id)
        return True

    monkeypatch.setattr("agentevolver.deploy.server.sandbox_manager.release", release)

    record = await manager.stop_site("a")

    assert captured == {
        "type": "host",
        "reuse_key": "a",
        "resource_id": "123:456:123",
    }
    assert record.status is SiteStatus.STOPPED
    assert record.url is None
    assert record.resource_id is None


@pytest.mark.asyncio
async def test_stop_refuses_to_claim_success_when_a_live_resource_cannot_be_verified(
    manager,
    monkeypatch,
):
    manager._sites["a"] = SiteRecord(
        site_id="a",
        runtime="static",
        status=SiteStatus.RUNNING,
        url="http://example.test",
        resource_id="stale",
    )

    async def not_released(*_args, **_kwargs):
        return False

    monkeypatch.setattr("agentevolver.deploy.server.sandbox_manager.release", not_released)
    monkeypatch.setattr(manager, "_url_reachable", lambda _url: _true())

    with pytest.raises(RuntimeError, match="refusing to report a false stop"):
        await manager.stop_site("a")
    assert manager._sites["a"].status is SiteStatus.RUNNING


@pytest.mark.asyncio
async def test_stop_keeps_an_unverified_resource_even_when_its_url_is_down(
    manager,
    monkeypatch,
):
    """An unavailable URL does not prove that its process/container is gone."""
    manager._sites["a"] = SiteRecord(
        site_id="a",
        runtime="static",
        status=SiteStatus.DETACHED,
        url="http://example.test",
        resource_id="persisted-resource",
    )

    async def not_released(*_args, **_kwargs):
        return False

    monkeypatch.setattr("agentevolver.deploy.server.sandbox_manager.release", not_released)
    monkeypatch.setattr(manager, "_url_reachable", lambda _url: _false())

    with pytest.raises(RuntimeError, match="unverified backend identity"):
        await manager.stop_site("a")
    assert manager._sites["a"].resource_id == "persisted-resource"
    assert manager._sites["a"].status is SiteStatus.DETACHED


@pytest.mark.asyncio
async def test_global_cleanup_does_not_stop_a_site_loaded_from_another_run(
    manager,
    monkeypatch,
):
    manager._sites["old"] = SiteRecord(
        site_id="old",
        runtime="static",
        status=SiteStatus.RUNNING,
        resource_id="persisted-resource",
    )
    calls = []

    async def release(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr("agentevolver.deploy.server.sandbox_manager.release", release)

    await manager.cleanup()

    assert calls == []
    assert manager._sites["old"].status is SiteStatus.RUNNING


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
