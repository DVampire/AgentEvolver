"""Deploy: profile resolution, backend selection, and the site registry.

The manager's contract is that it never knows React from FastAPI — a profile
turns intent into a spec, and the manager only executes it. So the parts worth
pinning down without a container are the ones that decide *what* gets executed:
spec resolution and its override precedence, which backend is chosen, and
whether the registry survives a restart.

Everything here is deliberately container-free; the acquire → upload → build →
start path needs a live sandbox and is not exercised.
"""

import json
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
    import agentevolver.deploy.default  # noqa: F401 — registers the built-in profiles

    server = DeploymentManagerServer()
    server._registry_path = str(tmp_path / "sites.json")
    server._initialized = True
    return server


# --------------------------------------------------------------- profiles
@pytest.mark.asyncio
async def test_the_built_in_profiles_are_discoverable(manager):
    profiles = await manager.list_profiles()
    assert {"static", "node", "python", "custom"} <= set(profiles)


def test_an_unknown_profile_names_the_ones_that_exist(manager):
    with pytest.raises(ValueError, match="No deploy profile 'nope'"):
        manager._profile("nope")


def test_a_static_site_needs_no_build_step(manager):
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime="static"))
    assert spec.build == []
    assert spec.port == 8000


@pytest.mark.parametrize("runtime", ["static", "node", "python"])
def test_no_profile_binds_loopback(manager, runtime):
    """A server on 127.0.0.1 is unreachable through expose_port — silent breakage.

    Profiles either say ``0.0.0.0`` outright or use a server that defaults to
    every interface; what none of them may do is pin loopback.
    """
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime=runtime))
    assert "127.0.0.1" not in spec.start
    assert "localhost" not in spec.start


@pytest.mark.parametrize("runtime", ["static", "node", "python"])
def test_every_profile_serves_the_port_it_reports(manager, runtime):
    """The recorded port is what becomes the site URL; the start command must agree."""
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime=runtime, port=7654))
    assert "7654" in spec.start or "$PORT" in spec.start


def test_the_custom_profile_refuses_to_guess_a_start_command(manager):
    with pytest.raises(ValueError, match="requires overrides.start"):
        manager._resolve_spec(DeployRequest(site_id="s", runtime="custom"))


def test_the_custom_profile_takes_everything_from_overrides(manager):
    spec = manager._resolve_spec(DeployRequest(
        site_id="s",
        runtime="custom",
        overrides={"image": "golang:1.22", "build": ["go build ."], "start": "./app"},
    ))
    assert spec.image == "golang:1.22"
    assert spec.build == ["go build ."]
    assert spec.start == "./app"


# ------------------------------------------------------- override precedence
def test_an_explicit_port_beats_the_profile_default(manager):
    spec = manager._resolve_spec(DeployRequest(site_id="s", runtime="static", port=9999))
    assert spec.port == 9999


def test_request_env_is_merged_over_the_profile_env(manager):
    spec = manager._resolve_spec(DeployRequest(
        site_id="s", runtime="static", env={"API_KEY": "secret"},
    ))
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
    spec = manager._resolve_spec(DeployRequest(
        site_id="s", runtime="static", overrides={field: value},
    ))
    assert getattr(spec, field) == value


def test_a_null_override_does_not_erase_the_profile_value(manager):
    spec = manager._resolve_spec(DeployRequest(
        site_id="s", runtime="static", overrides={"image": None},
    ))
    assert spec.image == "python:3.11-slim"


def test_a_health_override_is_accepted_as_a_plain_dict(manager):
    spec = manager._resolve_spec(DeployRequest(
        site_id="s", runtime="static", overrides={"health": {"type": "command", "command": "true"}},
    ))
    assert isinstance(spec.health, HealthCheck)
    assert spec.health.type == "command"
    assert spec.health.command == "true"


# ------------------------------------------------------- backend selection
@pytest.mark.parametrize(
    "choice, expected",
    [
        ("host", "host"),
        ("local", "host"),
        ("sandbox", "opensandbox"),
        ("opensandbox", "opensandbox"),
        ("docker", "opensandbox"),
        ("HOST", "host"),          # case-insensitive
        ("  host  ", "host"),      # and whitespace-tolerant
    ],
)
def test_the_backend_env_var_is_honoured(manager, monkeypatch, choice, expected):
    monkeypatch.setenv("DEPLOY_BACKEND", choice)
    assert manager._backend_kind() == expected


def test_auto_falls_back_to_the_host_when_no_container_runtime_is_reachable(manager, monkeypatch):
    """A deploy still has to work on a machine without Docker."""
    monkeypatch.setenv("DEPLOY_BACKEND", "auto")
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: False))
    assert manager._backend_kind() == "host"


def test_auto_prefers_the_container_when_one_is_reachable(manager, monkeypatch):
    monkeypatch.delenv("DEPLOY_BACKEND", raising=False)
    monkeypatch.setattr(manager, "_container_runtime_available", staticmethod(lambda: True))
    assert manager._backend_kind() == "opensandbox"


def test_a_remote_docker_host_counts_as_a_container_runtime(manager, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2375")
    assert manager._container_runtime_available() is True


def test_a_host_site_gets_its_own_directory_under_the_registry(manager, tmp_path):
    site_dir = manager._host_site_dir("my-site")
    assert site_dir.startswith(str(tmp_path))
    assert site_dir.endswith(os.path.join("sites", "my-site", "app"))


# ---------------------------------------------------------------- registry
def test_the_registry_survives_a_restart(manager, tmp_path):
    manager._sites["a"] = SiteRecord(site_id="a", runtime="static", url="http://x", port=8000)
    manager._save()

    reloaded = DeploymentManagerServer()
    reloaded._registry_path = str(tmp_path / "sites.json")
    reloaded._load()
    assert reloaded._sites["a"].url == "http://x"
    assert reloaded._sites["a"].runtime == "static"


def test_a_corrupt_registry_degrades_to_empty_rather_than_crashing(manager, tmp_path):
    """A bad registry must not stop the framework from starting."""
    (tmp_path / "sites.json").write_text("{ not json")
    manager._sites = {"stale": SiteRecord(site_id="stale", runtime="static")}
    manager._load()
    assert manager._sites == {}


def test_saving_without_a_registry_path_is_a_no_op():
    DeploymentManagerServer()._save()  # must not raise


# -------------------------------------------------------------- reconcile
@pytest.mark.asyncio
async def test_a_site_still_answering_is_reattached_as_running(manager, monkeypatch):
    manager._sites["a"] = SiteRecord(site_id="a", runtime="static", url="http://x",
                                     status=SiteStatus.RUNNING)
    monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _true()))
    await manager._reconcile_on_start()
    assert manager._sites["a"].status is SiteStatus.RUNNING


@pytest.mark.asyncio
async def test_a_site_that_no_longer_answers_becomes_redeployable(manager, monkeypatch):
    """The handle dies with the process; the record must say so, not lie."""
    manager._sites["a"] = SiteRecord(site_id="a", runtime="static", url="http://x",
                                     status=SiteStatus.RUNNING)
    monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _false()))
    await manager._reconcile_on_start()
    assert manager._sites["a"].status is SiteStatus.DETACHED


@pytest.mark.asyncio
async def test_deliberately_stopped_sites_are_left_alone(manager, monkeypatch):
    for status in (SiteStatus.STOPPED, SiteStatus.FAILED):
        manager._sites = {"a": SiteRecord(site_id="a", runtime="static", status=status)}
        monkeypatch.setattr(manager, "_url_reachable", staticmethod(lambda url: _false()))
        await manager._reconcile_on_start()
        assert manager._sites["a"].status is status


@pytest.mark.asyncio
async def test_a_site_without_a_url_is_never_considered_reachable(manager):
    assert await DeploymentManagerServer._url_reachable(None) is False
    assert await DeploymentManagerServer._url_reachable("") is False


async def _true():
    return True


async def _false():
    return False


# ------------------------------------------------------------------- types
def test_a_fresh_site_starts_pending():
    assert SiteRecord(site_id="a", runtime="static").status is SiteStatus.PENDING


def test_a_site_record_keeps_the_request_so_it_can_be_redeployed():
    request = DeployRequest(site_id="a", runtime="static", port=8080)
    record = SiteRecord(site_id="a", runtime="static", request=request.model_dump())
    assert DeployRequest(**record.request).port == 8080


def test_health_checks_default_to_probing_the_root_over_http():
    check = HealthCheck()
    assert (check.type, check.path) == ("http", "/")
    assert check.timeout_s > 0 and check.interval_s > 0


def test_gpu_is_off_by_default():
    assert ResourceSpec().gpu == 0


def test_a_spec_requires_the_fields_the_manager_executes():
    with pytest.raises(Exception):
        DeploymentSpec(runtime="static", image="x")  # no start, no port


def test_a_deploy_request_defaults_to_the_static_profile():
    assert DeployRequest(site_id="a").runtime == "static"
