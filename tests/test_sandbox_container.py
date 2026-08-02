"""The container paths, exercised against a real Docker daemon.

The rest of the sandbox suite asserts on the *arguments* the backend builds,
which is fast and runs everywhere but cannot tell you whether a container
actually came up, whether ``--network none`` really left it without a route, or
whether the egress relay lets the allowed host through and refuses the rest.
Those are the claims the isolation story rests on, so they are checked here
against the real thing.

Deselected by default (``-m 'not integration'``). To run::

    pytest tests/test_sandbox_container.py -m integration

Requires a reachable Docker daemon and the ``agentevolver/base`` image::

    docker build -t agentevolver/base:latest docker/base

The egress tests additionally reach ``example.com`` — they are skipped when the
host has no internet, since a failure there would say nothing about the relay.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio

from agentevolver.sandbox.default.docker import DockerSandbox, _container_name
from agentevolver.sandbox.server import sandbox_manager
from agentevolver.sandbox.types import SandboxConfig

pytestmark = pytest.mark.integration

IMAGE = os.environ.get("AGENTEVOLVER_TEST_IMAGE", "agentevolver/base:latest")
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[1])


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
    ).returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_ok(), reason=f"needs a Docker daemon and the {IMAGE} image",
)


def _internet_ok() -> bool:
    import socket

    try:
        socket.create_connection(("example.com", 80), timeout=5).close()
        return True
    except OSError:
        return False


requires_internet = pytest.mark.skipif(
    not _internet_ok(), reason="needs outbound internet to tell allow from deny",
)


def a_key(label: str) -> str:
    """A container name no other test or leftover run can collide with."""
    return f"pytest-{label}-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def box():
    """A started, network-less container, destroyed however the test ends."""
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=False, sandbox_key=a_key("box"), workdir="/workspace",
    ))
    await handle.start()
    try:
        yield handle
    finally:
        await handle.destroy()


# ------------------------------------------------------------------ lifecycle
@requires_docker
@pytest.mark.asyncio
async def test_a_started_container_is_alive_and_runs_a_command(box):
    assert await box.is_alive() is True
    result = await box.run_command("echo hello")
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"


@requires_docker
@pytest.mark.asyncio
async def test_destroying_removes_the_container(box):
    await box.destroy()
    assert await box.is_alive() is False
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{box.container_name}$"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert listed == ""


@requires_docker
@pytest.mark.asyncio
async def test_the_container_survives_an_image_whose_entrypoint_would_exit():
    """The sandbox is a place to exec into; the image's own entrypoint must not
    be able to take it down."""
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=False, sandbox_key=a_key("entrypoint"),
    ))
    await handle.start()
    try:
        for _ in range(3):
            assert (await handle.run_command("true")).exit_code == 0
        assert await handle.is_alive() is True
    finally:
        await handle.destroy()


@requires_docker
@pytest.mark.asyncio
async def test_starting_over_a_leftover_container_of_the_same_name():
    """A killed run leaves the name taken; the next start must reclaim it."""
    key = a_key("leftover")
    first = DockerSandbox(SandboxConfig(image=IMAGE, network=False, sandbox_key=key))
    await first.start()
    # Simulate a crashed run: the container is still there, the handle is gone.
    second = DockerSandbox(SandboxConfig(image=IMAGE, network=False, sandbox_key=key))
    try:
        await second.start()
        assert (await second.run_command("echo reclaimed")).stdout.strip() == "reclaimed"
    finally:
        await second.destroy()


@requires_docker
@pytest.mark.asyncio
async def test_destroy_is_idempotent(box):
    await box.destroy()
    await box.destroy()  # must not raise


@requires_docker
@pytest.mark.asyncio
async def test_a_container_is_named_from_its_key_not_its_image():
    """Two sandboxes sharing an image would otherwise collide on one name."""
    key_a, key_b = a_key("named-a"), a_key("named-b")
    a = DockerSandbox(SandboxConfig(image=IMAGE, sandbox_key=key_a))
    b = DockerSandbox(SandboxConfig(image=IMAGE, sandbox_key=key_b))
    assert a.container_name != b.container_name
    assert a.container_name == _container_name(key_a)


# -------------------------------------------------------------------- results
@requires_docker
@pytest.mark.asyncio
async def test_a_nonzero_exit_is_an_answer_not_a_failure(box):
    """The command ran and said no — that is a result the agent must see, not an
    infrastructure error."""
    result = await box.run_command("exit 3")
    assert result.success is True
    assert result.exit_code == 3


@requires_docker
@pytest.mark.asyncio
async def test_stdout_and_stderr_come_back_separately(box):
    result = await box.run_command("echo out; echo err >&2")
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


@requires_docker
@pytest.mark.asyncio
async def test_a_command_that_hangs_is_cut_off_and_says_so(box):
    result = await box.run_command("sleep 60", timeout=3)
    assert result.success is False
    assert "3" in (result.error or "") + result.stderr


@requires_docker
@pytest.mark.asyncio
async def test_the_environment_reaches_the_command(box):
    result = await box.run_command("echo $MY_VAR", env={"MY_VAR": "from-the-caller"})
    assert result.stdout.strip() == "from-the-caller"


# ---------------------------------------------------------------------- files
@requires_docker
@pytest.mark.asyncio
async def test_a_written_file_reads_back_byte_for_byte(box):
    await box.write_file("/workspace/note.txt", "content with 中文 and 'quotes'")
    assert await box.read_file("/workspace/note.txt") == "content with 中文 and 'quotes'"


@requires_docker
@pytest.mark.asyncio
async def test_binary_content_survives_the_round_trip(box):
    payload = bytes(range(256))
    await box.write_file("/workspace/blob.bin", payload)
    assert await box.read_bytes("/workspace/blob.bin") == payload


@requires_docker
@pytest.mark.asyncio
async def test_writing_creates_the_parent_directory(box):
    await box.write_file("/workspace/deep/nested/file.txt", "x")
    assert (await box.run_command("cat /workspace/deep/nested/file.txt")).stdout.strip() == "x"


@requires_docker
@pytest.mark.asyncio
async def test_the_requested_mode_is_applied_not_left_to_the_umask(box):
    """A shell redirect takes the umask, which made this backend and OpenSandbox
    disagree on the result of an identical call."""
    await box.write_file("/workspace/script.sh", "#!/bin/sh\necho ran\n", mode=0o755)
    result = await box.run_command("stat -c '%a' /workspace/script.sh")
    assert result.stdout.strip() == "755"


@requires_docker
@pytest.mark.asyncio
async def test_reading_a_missing_file_raises_rather_than_returning_empty(box):
    with pytest.raises(IOError):
        await box.read_file("/workspace/not-there.txt")


@requires_docker
@pytest.mark.asyncio
async def test_a_mounted_directory_is_visible_inside(tmp_path):
    (tmp_path / "given.txt").write_text("from the host", encoding="utf-8")
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=False, sandbox_key=a_key("mount"),
        mounts={str(tmp_path): "/mounted"}, workdir="/mounted",
    ))
    await handle.start()
    try:
        assert (await handle.read_file("/mounted/given.txt")) == "from the host"
        await handle.run_command("echo back > /mounted/written.txt")
        assert (tmp_path / "written.txt").read_text().strip() == "back"
    finally:
        await handle.destroy()


# -------------------------------------------------------------------- network
@requires_docker
@pytest.mark.asyncio
async def test_a_closed_network_leaves_no_route_out(box):
    """``--network none`` is the claim behind "this work had no internet access"."""
    result = await box.run_command("getent hosts example.com || echo NO-ROUTE", timeout=30)
    assert "NO-ROUTE" in result.stdout


@requires_docker
@pytest.mark.asyncio
async def test_a_closed_network_has_only_loopback(box):
    result = await box.run_command("ip -o addr show 2>/dev/null | awk '{print $2}' | sort -u")
    interfaces = set(result.stdout.split())
    assert interfaces <= {"lo"}, interfaces


@requires_docker
@requires_internet
@pytest.mark.asyncio
async def test_an_open_network_can_reach_the_internet():
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=True, sandbox_key=a_key("open"),
    ))
    await handle.start()
    try:
        result = await handle.run_command(
            "curl -sS -o /dev/null -w '%{http_code}' -m 30 http://example.com/", timeout=60,
        )
        assert result.stdout.strip() == "200"
    finally:
        await handle.destroy()


# --------------------------------------------------------------- egress relay
@pytest_asyncio.fixture
async def gated():
    """A network-less sandbox whose only route out is the policy-checking relay.

    The framework checkout and the running interpreter are mounted at their host
    paths because the forwarder inside the container is started from them.
    """
    key = a_key("egress")
    handle = await sandbox_manager.acquire(
        "docker", reuse_key=key,
        image=IMAGE, network=False, allow_hosts=["example.com"],
        mounts={REPO: "/AgentEvolver", sys.prefix: sys.prefix},
        workdir="/workspace",
    )
    try:
        yield handle
    finally:
        await sandbox_manager.release("docker", reuse_key=key)


async def _status(handle, url):
    result = await handle.run_command(
        f"curl -sS -o /dev/null -w '%{{http_code}}' -m 30 {url}", timeout=60,
    )
    return result.stdout.strip()


@requires_docker
@requires_internet
@pytest.mark.asyncio
async def test_an_allowed_host_is_reachable_through_the_relay(gated):
    """This is what lets an agent brain call a model endpoint while the work it
    supervises has no internet — a single boolean cannot express it."""
    assert await _status(gated, "http://example.com/") == "200"


@requires_docker
@requires_internet
@pytest.mark.asyncio
async def test_an_unlisted_host_is_refused(gated):
    assert await _status(gated, "http://neverdns.invalid/") == "403"


@requires_docker
@requires_internet
@pytest.mark.asyncio
async def test_every_attempt_is_on_the_record(gated):
    """A refusal nobody can see is indistinguishable from one that never happened."""
    await _status(gated, "http://example.com/")
    await _status(gated, "http://neverdns.invalid/")

    audit = sandbox_manager.egress_audit(gated)
    assert audit is not None
    assert audit["attempts"] >= 2
    denied = {entry["host"] for entry in audit["denied"]}
    assert "neverdns.invalid" in denied
    assert "example.com" not in denied
    assert audit["allowed_hosts"] == ["example.com"]


@requires_docker
@pytest.mark.asyncio
async def test_an_unwatched_sandbox_reports_no_audit_rather_than_an_empty_one(box):
    """An empty denial list would read as evidence of isolation never applied."""
    assert sandbox_manager.egress_audit(box) is None


@requires_docker
@pytest.mark.asyncio
async def test_a_policy_without_a_mounted_checkout_fails_loudly():
    """A sandbox that believes it has a route and does not would burn its whole
    budget on connection errors."""
    key = a_key("no-checkout")
    with pytest.raises(Exception, match="framework checkout"):
        await sandbox_manager.acquire(
            "docker", reuse_key=key,
            image=IMAGE, network=False, allow_hosts=["example.com"],
        )

@requires_docker
@pytest.mark.asyncio
async def test_a_failed_start_leaves_no_container_behind():
    """``start`` creates the container and only then starts the forwarder, so a
    policy whose forwarder cannot run used to leave one running with nothing
    holding it — and nothing is cached yet, so ``release`` would never find it."""
    key = a_key("failed-start")
    with pytest.raises(Exception, match="framework checkout"):
        await sandbox_manager.acquire(
            "docker", reuse_key=key,
            image=IMAGE, network=False, allow_hosts=["example.com"],
        )
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{_container_name(key)}$"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert listed == ""


@requires_docker
@pytest.mark.asyncio
async def test_a_failed_start_leaves_no_relay_socket_behind():
    """A stale socket is a listener for a sandbox that never existed."""
    from agentevolver.sandbox.relay import default_socket_path

    key = a_key("failed-relay")
    with pytest.raises(Exception, match="framework checkout"):
        await sandbox_manager.acquire(
            "docker", reuse_key=key,
            image=IMAGE, network=False, allow_hosts=["example.com"],
        )
    assert not os.path.exists(str(default_socket_path(key)))


# -------------------------------------------------------------------- manager
@requires_docker
@pytest.mark.asyncio
async def test_a_reuse_key_keeps_the_container_warm():
    key = a_key("warm")
    try:
        first = await sandbox_manager.acquire("docker", reuse_key=key, image=IMAGE, network=False)
        await first.run_command("echo marker > /tmp/marker")
        second = await sandbox_manager.acquire("docker", reuse_key=key, image=IMAGE, network=False)
        assert second is first
        assert (await second.run_command("cat /tmp/marker")).stdout.strip() == "marker"
    finally:
        await sandbox_manager.release("docker", reuse_key=key)


@requires_docker
@pytest.mark.asyncio
async def test_different_keys_get_different_containers():
    keys = [a_key("sep-a"), a_key("sep-b")]
    try:
        a, b = [
            await sandbox_manager.acquire("docker", reuse_key=k, image=IMAGE, network=False)
            for k in keys
        ]
        assert a is not b
        await a.run_command("echo only-in-a > /tmp/marker")
        assert (await b.run_command("cat /tmp/marker 2>/dev/null || echo absent")).stdout.strip() == "absent"
    finally:
        for k in keys:
            await sandbox_manager.release("docker", reuse_key=k)


@requires_docker
@pytest.mark.asyncio
async def test_releasing_destroys_the_container():
    key = a_key("released")
    handle = await sandbox_manager.acquire("docker", reuse_key=key, image=IMAGE, network=False)
    await sandbox_manager.release("docker", reuse_key=key)
    assert await handle.is_alive() is False


@requires_docker
@pytest.mark.asyncio
async def test_a_dead_container_is_not_handed_back_from_the_cache():
    """Something outside the framework can kill a container; the next acquire
    must notice rather than hand back a handle to nothing."""
    key = a_key("revived")
    try:
        first = await sandbox_manager.acquire("docker", reuse_key=key, image=IMAGE, network=False)
        subprocess.run(["docker", "rm", "-f", first.container_name], capture_output=True)
        second = await sandbox_manager.acquire("docker", reuse_key=key, image=IMAGE, network=False)
        assert await second.is_alive() is True
    finally:
        await sandbox_manager.release("docker", reuse_key=key)


# ------------------------------------------------------------------- identity
@requires_docker
@pytest.mark.asyncio
async def test_the_requested_user_is_who_the_command_runs_as():
    """An image whose files are readable only by its own user relies on this; a
    sandbox that silently runs as root hands back access the image withheld."""
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=False, sandbox_key=a_key("identity"), user="1000:1000",
    ))
    await handle.start()
    try:
        assert (await handle.run_command("id -u")).stdout.strip() == "1000"
    finally:
        await handle.destroy()


@requires_docker
@pytest.mark.asyncio
async def test_the_workdir_is_where_a_command_starts():
    handle = DockerSandbox(SandboxConfig(
        image=IMAGE, network=False, sandbox_key=a_key("workdir"), workdir="/tmp",
    ))
    await handle.start()
    try:
        assert (await handle.run_command("pwd")).stdout.strip() == "/tmp"
    finally:
        await handle.destroy()


# --------------------------------------------------------------------- reaper
@requires_docker
@pytest.mark.asyncio
async def test_orphaned_processes_are_reaped_rather_than_left_as_zombies(box):
    """51 zombies accumulated in the first 13 minutes of one measured task; a
    long run would eventually exhaust the pid limit."""
    await box.run_command(
        "sh -c '(sleep 30 &) ; exit 0'"
    )
    await box.run_command("sleep 1")
    result = await box.run_command("ps -eo stat= | grep -c '^Z' || true")
    assert int(result.stdout.strip() or 0) == 0
