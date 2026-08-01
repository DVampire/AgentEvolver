"""The docker sandbox backend.

These assert on the `docker` arguments the backend builds, because that is where its
guarantees live: no network interface, the identity the image expects, the mounts a task
needs, and one route out that the sandbox cannot reconfigure.
"""

import asyncio

from agentevolver.sandbox.default.docker import DockerSandbox, _container_name
from agentevolver.sandbox.types import SandboxConfig


def _args(**config):
    config.setdefault("image", "example/image:tag")
    return DockerSandbox(SandboxConfig(**config))._run_args()


def _pairs(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_network_off_means_no_interface():
    """`--network none` is the guarantee: an unlisted host is not filtered, it is
    unreachable. An allowlist must not quietly restore an interface — allowed traffic
    arrives through the mounted relay socket instead."""
    assert "none" in _pairs(_args(network=False), "--network")
    assert "none" in _pairs(_args(network=False, allow_hosts=["api.example.com"]), "--network")
    assert _pairs(_args(network=True), "--network") == []


def test_identity_and_workdir_are_passed_through():
    """An image whose files are readable only by its own user relies on the process
    running as that user; a sandbox that silently runs as root hands back access the image
    was built to withhold."""
    args = _args(user="agent", workdir="/workspace")
    assert _pairs(args, "--user") == ["agent"]
    assert _pairs(args, "--workdir") == ["/workspace"]
    assert _pairs(_args(), "--user") == []


def test_mounts_are_passed_through():
    args = _args(mounts={"/host/repo": "/AgentEvolver", "/host/env": "/host/env"})
    assert "/host/repo:/AgentEvolver" in _pairs(args, "-v")
    assert "/host/env:/host/env" in _pairs(args, "-v")


def test_an_egress_socket_brings_the_socket_and_the_proxy_variables():
    """Set for the whole container on purpose: the point is that everything inside it —
    the agent's curl and git as much as the framework's own model calls — has one route
    out, and that route checks the policy."""
    args = _args(network=False, egress_socket="/tmp/agentevolver-egress/x.sock", egress_port=9000)
    assert "/tmp/agentevolver-egress/x.sock:/run/egress.sock" in _pairs(args, "-v")
    env = _pairs(args, "-e")
    assert "HTTPS_PROXY=http://127.0.0.1:9000" in env
    assert "HTTP_PROXY=http://127.0.0.1:9000" in env
    # Lowercase too: curl and git read those, and they are what an agent reaches for.
    assert "https_proxy=http://127.0.0.1:9000" in env
    assert any(e.startswith("NO_PROXY=127.0.0.1") for e in env)


def test_no_proxy_variables_without_a_policy():
    """A sandbox with nothing to enforce should not be told to route through a proxy that
    does not exist."""
    env = _pairs(_args(network=True), "-e")
    assert not any(e.startswith(("HTTP_PROXY", "HTTPS_PROXY")) for e in env)


def test_caller_env_wins_over_the_injected_proxy():
    args = _args(network=False, egress_socket="/tmp/x.sock", env={"HTTPS_PROXY": "http://other:1"})
    assert "HTTPS_PROXY=http://other:1" in _pairs(args, "-e")


def test_the_container_outlives_the_image_entrypoint():
    """A sandbox is a place to exec into; an image whose entrypoint exits would otherwise
    take the sandbox down with it."""
    args = _args()
    assert _pairs(args, "--entrypoint") == ["sleep"]
    assert args[-1] == "infinity"
    assert "-d" in args


def test_container_names_come_from_the_sandbox_key_not_the_image():
    """Two sandboxes sharing an image would otherwise collide on one container name."""
    one = DockerSandbox(SandboxConfig(image="same/image", sandbox_key="task-a"))
    two = DockerSandbox(SandboxConfig(image="same/image", sandbox_key="task-b"))
    assert one.container_name != two.container_name
    # Stable across constructions, so a leftover from a killed run can be removed by exact
    # name — never by `--filter ancestor=`, which matches unrelated containers.
    again = DockerSandbox(SandboxConfig(image="same/image", sandbox_key="task-a"))
    assert again.container_name == one.container_name
    assert one.container_name.startswith("agentevolver-sbx-")


def test_container_name_survives_an_awkward_key():
    """An image reference is full of characters a container name cannot carry."""
    name = _container_name("programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom_v6")
    assert "/" not in name and ":" not in name
    # Legible in `docker ps`, and no dangling separator left by truncation.
    assert len(name) <= 64
    assert "-" + "-" not in name and not name.rstrip("0123456789abcdef").endswith((".-", "_-", "--"))


def test_removal_is_by_exact_name():
    """Never `--filter ancestor=<image>`: that matches every container built from the
    image, including long-lived ones belonging to someone else."""
    calls = []

    async def fake_run(*args, timeout=300.0):
        calls.append(args)
        return 0, "", ""

    import agentevolver.sandbox.default.docker as mod

    original, mod._run = mod._run, fake_run
    try:
        sandbox = DockerSandbox(SandboxConfig(image="example/image", sandbox_key="k"))
        asyncio.run(sandbox.destroy())
    finally:
        mod._run = original

    assert calls, "destroy issued no docker command"
    rm = calls[-1]
    assert rm[1:3] == ("rm", "-f")
    assert rm[-1] == sandbox.container_name
    assert not any("ancestor" in str(a) for a in rm)


def test_a_nonzero_exit_is_an_answer_not_a_tool_failure():
    """`grep` finding nothing and a build failing are results the caller must be able to
    read. Only being unable to run the command at all is a failure."""
    async def fake_run(*_args, timeout=300.0):
        return 1, "stdout text", "stderr text"

    import agentevolver.sandbox.default.docker as mod

    original, mod._run = mod._run, fake_run
    try:
        sandbox = DockerSandbox(SandboxConfig(image="example/image", sandbox_key="k"))
        sandbox._started = True
        result = asyncio.run(sandbox.run_command("grep -q nothing file"))
    finally:
        mod._run = original

    assert result.success is True
    assert result.exit_code == 1
    assert result.stdout == "stdout text"


def test_a_timeout_is_reported_as_a_failure_naming_the_limit():
    async def fake_run(*_args, timeout=300.0):
        raise asyncio.TimeoutError

    import agentevolver.sandbox.default.docker as mod

    original, mod._run = mod._run, fake_run
    try:
        sandbox = DockerSandbox(SandboxConfig(image="example/image", sandbox_key="k"))
        sandbox._started = True
        result = asyncio.run(sandbox.run_command("sleep 99", timeout=5))
    finally:
        mod._run = original

    assert result.success is False
    assert "5s" in (result.error or "")


def test_an_egress_policy_without_a_mounted_framework_fails_loudly():
    """The forwarder that carries the traffic runs out of the mounted checkout. Silently
    starting without it would leave the sandbox believing it has a route to the model
    endpoint and burning its budget on connection errors."""
    sandbox = DockerSandbox(SandboxConfig(
        image="example/image", sandbox_key="k", network=False, egress_socket="/tmp/x.sock",
    ))
    try:
        asyncio.run(sandbox._start_forwarder())
    except RuntimeError as e:
        assert "no framework checkout is mounted" in str(e)
    else:
        raise AssertionError("expected a RuntimeError")


def test_the_forwarder_is_run_as_a_script_with_isolated_sys_path():
    """Two traps at once. `-m` executes the package __init__ chain, which materialises
    output/ and extension/ relative to the cwd — observed landing inside the task's
    /workspace, where they would have shipped in the submission. And running the file
    normally prepends its own directory to sys.path, where `agentevolver/sandbox/types.py`
    shadows the standard library's `types`; that broke even `import argparse`, which
    reaches `types` via `re`. `-P` drops the script directory from sys.path."""
    calls = []

    async def fake_run(*args, timeout=300.0):
        calls.append(args)
        return 0, "", ""

    import agentevolver.sandbox.default.docker as mod

    original, mod._run = mod._run, fake_run
    try:
        sandbox = DockerSandbox(SandboxConfig(
            image="example/image", sandbox_key="k", network=False,
            egress_socket="/tmp/x.sock", mounts={"/host/repo": "/AgentEvolver"},
        ))
        sandbox._started = True
        try:
            asyncio.run(sandbox._start_forwarder())
        except RuntimeError:
            pass  # the readiness probe cannot pass against a stub
    finally:
        mod._run = original

    exec_call = next(c for c in calls if "exec" in c)
    command = exec_call[-1]
    assert " -P " in command
    assert "/AgentEvolver/agentevolver/sandbox/forwarder.py" in command
    assert "-m agentevolver" not in command
    assert "--workdir" in exec_call and "/tmp" in exec_call


def test_the_container_gets_a_process_reaper():
    """PID 1 is `sleep infinity`, which does not reap. Without an init, anything a command
    leaves behind — the grandchildren of a killed process group, a backgrounded server, a
    build's stragglers — is reparented to it and stays a zombie for the life of the
    sandbox. 51 accumulated in the first 13 minutes of one task."""
    args = _args(image="img")
    assert "--init" in args
    assert args.index("--init") < args.index("img"), "flags must precede the image"
