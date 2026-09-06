"""A sandbox that is just a container, driven by the `docker` CLI.

Chosen over the opensandbox backend when the point is to run work *in a specific image*
rather than in a general-purpose worker: it takes `--user`, arbitrary bind mounts and
`--network none` directly, so a task image with its own toolchain and its own file
ownership behaves the way it was built to.

The pairing that motivates it: a benchmark task ships a 3GB image carrying that project's
compiler, headers and reference binary. Running the agent's shell anywhere else means it
probes one toolchain and builds with another — and no general-purpose image can carry 201
projects' build dependencies. So the agent runs *in the task's own image*, which needs the
image named, the mounts placed, and the identity honoured.

Only `run_command` and lifecycle are implemented: the base class builds `write_file` /
`read_file` / `read_bytes` on top of command execution, so there is no second code path
that can disagree with the first about what the filesystem looks like.

Egress: when the manager has decided this sandbox needs a policy enforced, it stamps
`config.egress_socket` and the socket is mounted in, a forwarder started on loopback, and
the proxy variables exported — see :mod:`agentevolver.sandbox.relay`. The container gets
no network interface in that case, so the relay is not a filter over a working network; it
is the only route that exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
from datetime import timedelta
from typing import Any, Dict, List, Optional, Union

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.base import to_host_path
from agentevolver.sandbox.types import ExecResult, Sandbox, SandboxConfig

#: Where a mounted egress socket appears inside the container.
_EGRESS_SOCKET_IN_CONTAINER = "/run/egress.sock"

#: Prefix for every container this backend creates. Containers are only ever removed by
#: exact name — never by `--filter ancestor=`, which matches unrelated long-lived
#: containers built from the same image and has destroyed one.
_NAME_PREFIX = "agentevolver-sbx"

_DOCKER = os.environ.get("AGENTEVOLVER_DOCKER", "docker")

#: A login shell reads ``/etc/profile``, and Debian's sets ``PATH`` to a fixed default —
#: discarding whatever the image's own ``ENV PATH`` put there. For an image whose toolchain
#: lives outside the standard directories that silently removes the toolchain: an image
#: derived from ``golang:1.16-bullseye`` carries ``/usr/local/go/bin`` on the image PATH and
#: nowhere else, so every ``go`` call inside the sandbox failed with ``go: command not
#: found`` while ``docker run sh -c`` on the same image found it. This module exists so that
#: "a task image with its own toolchain behaves the way it was built to"; a login shell that
#: drops the image's PATH is that promise broken in the one place nothing checks.
#:
#: The image's PATH is passed in under its own name — the profile overwrites ``PATH``, not
#: this — and put back in front, so anything the profile added stays reachable behind it.
_IMAGE_PATH_VAR = "_AE_IMAGE_PATH"


def _restore_image_path(image_path: Optional[str]) -> str:
    """A prologue re-prepending the image's own PATH; empty when it could not be read."""
    if not image_path:
        return ""
    return (
        f'if [ -n "${{{_IMAGE_PATH_VAR}:-}}" ]; then '
        f'PATH="${_IMAGE_PATH_VAR}${{PATH:+:$PATH}}"; export PATH; fi\n'
    )


def _container_name(key: str) -> str:
    """A readable, stable, collision-resistant container name for `key`.

    Readable so `docker ps` is legible, hashed so two keys sharing a truncated prefix
    cannot land on one container, and stable so a leftover from a killed run can be
    removed by exact name next time.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", key or "sandbox")[:28].strip("-._") or "sandbox"
    digest = hashlib.sha256((key or "sandbox").encode("utf-8")).hexdigest()[:10]
    return f"{_NAME_PREFIX}-{slug}-{digest}"


async def _run(*args: str, timeout: float = 300.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


@SANDBOX.register_module(name="docker", force=True)
class DockerSandbox(Sandbox):
    """A long-lived container; commands run through `docker exec`."""

    name: str = "docker"
    description: str = "A container from a named image, with explicit mounts, identity and egress policy."

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config, **kwargs)
        if not self.config.image:
            raise ValueError("DockerSandbox requires an image")
        # Named from the manager-stamped key, not from the image: two sandboxes sharing an
        # image would otherwise collide on one container name.
        self._name = _container_name(self.config.sandbox_key or self.config.image)
        self._forwarder_started = False
        self._image_path_value: Optional[str] = None

    @property
    def container_name(self) -> str:
        return self._name

    @property
    def resource_id(self) -> Optional[str]:
        return self._name

    @classmethod
    async def destroy_resource(cls, resource_id: str) -> bool:
        code, _out, err = await _run(_DOCKER, "rm", "-f", resource_id, timeout=120)
        return code == 0 or "No such container" in err

    @property
    def container_workspace(self) -> Optional[str]:
        return self.config.workdir

    # ------------------------------------------------------------- lifecycle
    def _run_args(self) -> List[str]:
        # `--init` because PID 1 here is `sleep infinity`, which does not reap. Anything a
        # command leaves behind — a killed process group's grandchildren, a backgrounded
        # server, a build's stragglers — is reparented to PID 1 and stays a zombie forever.
        # Measured: 51 of them accumulated in the first 13 minutes of one task, and a long
        # run would eventually exhaust the pid limit. `--init` puts a real reaper there.
        args = [_DOCKER, "run", "-d", "--init", "--name", self._name]

        for container_port, host_port in (self.config.publish_ports or {}).items():
            args += ["-p", (f"{host_port}:{container_port}" if host_port
                            else f"{container_port}")]

        # No interface at all when the network is off. An allowlist does not change that:
        # allowed hosts arrive through the mounted relay socket, so there is nothing for a
        # NIC to carry and nothing for a misconfigured filter to leak.
        if not self.config.network:
            args += ["--network", "none"]

        if self.config.user:
            args += ["--user", self.config.user]
        if self.config.workdir:
            args += ["--workdir", self.config.workdir]

        for host_path, container_path in (self.config.mounts or {}).items():
            args += ["-v", f"{to_host_path(host_path)}:{container_path}:rw"]

        env = dict(self.config.env or {})
        if self.config.egress_socket:
            args += ["-v", f"{to_host_path(self.config.egress_socket)}:{_EGRESS_SOCKET_IN_CONTAINER}:rw"]
            proxy = f"http://127.0.0.1:{self.config.egress_port}"
            # Set for the whole container, deliberately: the point is that *everything*
            # inside it — the agent's curl and git as much as the framework's own model
            # calls — has one route out, and that route checks the policy.
            env.setdefault("HTTP_PROXY", proxy)
            env.setdefault("HTTPS_PROXY", proxy)
            env.setdefault("http_proxy", proxy)
            env.setdefault("https_proxy", proxy)
            env.setdefault("NO_PROXY", "127.0.0.1,localhost")
            env.setdefault("no_proxy", "127.0.0.1,localhost")
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]

        # `sleep infinity` rather than the image's entrypoint: this is a place to exec
        # into, and an image whose entrypoint exits would otherwise take the sandbox with
        # it. Overriding the entrypoint too, since an image with its own ENTRYPOINT would
        # treat the command as arguments to it. A caller that needs something else as PID
        # 1 — systemd, for a desktop — says so explicitly.
        if self.config.entrypoint:
            args += ["--entrypoint", self.config.entrypoint[0], self.config.image]
            args += list(self.config.entrypoint[1:])
        else:
            args += ["--entrypoint", "sleep", self.config.image, "infinity"]
        return args

    async def start(self) -> None:
        if self._started:
            return

        # A container left behind by a killed run holds the name and makes `run` fail.
        # Removed by exact name only.
        await _run(_DOCKER, "rm", "-f", self._name, timeout=60)

        code, out, err = await _run(*self._run_args(), timeout=600)
        if code != 0:
            raise RuntimeError(f"docker run failed for {self._name}: {(err or out).strip()}")
        self._started = True
        logger.info(
            f"| 📦 DockerSandbox {self._name} up "
            f"(image={self.config.image}, network={'on' if self.config.network else 'none'}"
            f"{', egress relay' if self.config.egress_socket else ''})"
        )
        if self.config.egress_socket:
            await self._start_forwarder()

    async def _start_forwarder(self) -> None:
        """Start the loopback->socket forwarder inside the container.

        Runs the module out of whatever framework checkout is mounted, using the
        interpreter that is running this process — in this layout both are visible inside
        the container at their host paths. Failure to start is fatal rather than silent: a
        sandbox that believes it has a route to the model endpoint and does not would burn
        its whole budget on connection errors.
        """
        import sys

        framework = self._framework_path()
        if not framework:
            raise RuntimeError(
                f"sandbox {self._name} has an egress policy but no framework checkout is "
                f"mounted, so the forwarder that carries its traffic cannot be started. "
                f"Mount the checkout (…: '/AgentEvolver') when acquiring."
            )
        script = f"{framework.rstrip('/')}/agentevolver/sandbox/forwarder.py"

        # Run the file directly rather than `-m agentevolver.sandbox.forwarder`: the module
        # form executes the package's __init__ chain, which materialises the framework's
        # output/ and extension/ directories relative to the working directory. That put
        # `output/` and `extension/` inside the task's /workspace — where they would then
        # have shipped in the submission. The forwarder is deliberately import-free, so
        # running it as a script is also the truer invocation.
        #
        # `-P` is not optional. Running a script normally prepends its own directory to
        # sys.path, and this one lives beside `agentevolver/sandbox/types.py`, which
        # shadows the standard library's `types` — enough to make even `import argparse`
        # fail, since it reaches `types` through `re`. `-P` leaves the script's directory
        # off sys.path; it needs Python 3.11+, and failing loudly on an older interpreter
        # beats silently reintroducing the shadowing.
        #
        # --workdir /tmp, belt and braces: nothing started here has any business writing
        # into the workspace under study.
        command = (
            f"{shlex.quote(sys.executable)} -P {shlex.quote(script)} "
            f"--socket {shlex.quote(_EGRESS_SOCKET_IN_CONTAINER)} "
            f"--port {int(self.config.egress_port)} >/tmp/egress-forwarder.log 2>&1 &"
        )
        code, _out, err = await _run(
            _DOCKER, "exec", "-d", "--workdir", "/tmp",
            self._name, "bash", "-lc", command, timeout=60,
        )
        if code != 0:
            raise RuntimeError(
                f"could not start the egress forwarder in {self._name}: {err.strip()}. "
                f"It runs {sys.executable} from inside the container, so that interpreter "
                f"and the framework checkout both have to be mounted at their host paths "
                f"for a sandbox with an egress policy."
            )

        # Wait for the port rather than assuming: the first model call would otherwise
        # race the forwarder and fail for a reason that looks like a network fault.
        probe = (
            "python3 - <<'EOF'\n"
            "import socket, sys, time\n"
            f"deadline = time.time() + 20\n"
            "while time.time() < deadline:\n"
            "    s = socket.socket(); s.settimeout(0.3)\n"
            f"    if s.connect_ex(('127.0.0.1', {int(self.config.egress_port)})) == 0:\n"
            "        sys.exit(0)\n"
            "    time.sleep(0.25)\n"
            "sys.exit(1)\n"
            "EOF"
        )
        result = await self.run_command(probe, workspace_root="/tmp", timeout=40)
        if not result.success:
            log = await self.run_command("cat /tmp/egress-forwarder.log")
            raise RuntimeError(
                f"egress forwarder in {self._name} never accepted connections on port "
                f"{self.config.egress_port}: {log.stdout.strip() or log.as_message()}"
            )
        self._forwarder_started = True

    def _framework_path(self) -> str:
        """Where the framework checkout is mounted inside the container, if it is."""
        for _host, container in (self.config.mounts or {}).items():
            if os.path.basename(container.rstrip("/")).lower() in ("agentevolver", "agent-evolver"):
                return container
        return ""

    async def destroy(self) -> None:
        # By exact name. Never `--filter ancestor=<image>`: that matches every container
        # built from the image, including long-lived ones belonging to someone else.
        code, _out, err = await _run(_DOCKER, "rm", "-f", self._name, timeout=120)
        if code != 0 and "No such container" not in err:
            raise RuntimeError(f"could not remove Docker sandbox {self._name}: {err.strip()}")
        self._started = False

    async def is_alive(self) -> bool:
        if not self._started:
            return False
        code, out, _err = await _run(
            _DOCKER, "inspect", "-f", "{{.State.Running}}", self._name, timeout=30
        )
        return code == 0 and out.strip() == "true"

    # ------------------------------------------------------------- execution
    async def _image_path(self) -> Optional[str]:
        """The container's own ``PATH``, read once from its config and cached.

        Read from the container rather than the image so that an ``-e PATH=...`` at
        creation is honoured too. A failure returns None and the prologue becomes empty:
        a missing PATH is worth losing, a broken command is not.
        """
        if self._image_path_value is not None:
            return self._image_path_value or None
        self._image_path_value = ""
        try:
            code, out, _err = await _run(
                _DOCKER, "inspect", "--format",
                "{{range .Config.Env}}{{println .}}{{end}}", self._name, timeout=30,
            )
        except asyncio.TimeoutError:
            return None
        if code == 0:
            for line in out.splitlines():
                if line.startswith("PATH="):
                    self._image_path_value = line[len("PATH="):].strip()
                    break
        return self._image_path_value or None

    async def run_command(
        self,
        command: str,
        *,
        workspace_root: Optional[str] = None,
        timeout: Optional[Union[int, timedelta]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecResult:
        if not self._started:
            return ExecResult(success=False, error=f"sandbox {self._name} is not started")

        seconds = timeout.total_seconds() if isinstance(timeout, timedelta) else (timeout or 600)
        args = [_DOCKER, "exec"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        workdir = workspace_root or self.config.workdir
        if workdir:
            args += ["--workdir", workdir]
        image_path = await self._image_path()
        if image_path:
            args += ["-e", f"{_IMAGE_PATH_VAR}={image_path}"]
        args += [self._name, "bash", "-lc", _restore_image_path(image_path) + command]

        try:
            code, out, err = await _run(*args, timeout=float(seconds))
        except asyncio.TimeoutError:
            return ExecResult(
                success=False, exit_code=None, stdout="", stderr="",
                error=f"command exceeded {seconds:.0f}s in sandbox {self._name}",
            )
        # A non-zero exit is an *answer* — `grep` finding nothing, a build failing — not a
        # failure of the tool that ran it. Only being unable to run the command at all is.
        return ExecResult(success=True, stdout=out, stderr=err, exit_code=code)

    async def expose_port(self, port: int) -> str:
        """The host address a published container port answers on.

        Docker assigns the host side at creation, so this reads back what was actually
        bound rather than assuming the numbers match — `publish_ports` with a host port of
        0 asks Docker to choose, and it does.
        """
        code, out, _err = await _run(
            _DOCKER, "port", self._name, str(port), timeout=30,
        )
        mapped = (out or "").strip().splitlines()
        if code != 0 or not mapped:
            raise RuntimeError(
                f"port {port} is not published on {self._name}; a published port is fixed "
                f"when the container is created — list it in config.publish_ports."
            )
        # e.g. "0.0.0.0:49154" or "[::]:49154"
        host_port = mapped[0].rsplit(":", 1)[-1].strip()
        return f"http://127.0.0.1:{host_port}"
