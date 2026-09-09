"""Owned Docker pair and a persistent MCP client with one AnyIO context owner."""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentevolver.tool.default.workspace.container import bind_container, unbind_container


async def docker_command(*args: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        os.environ.get("AGENTEVOLVER_DOCKER", "docker"), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        with suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-4000:])
    return stdout.decode(errors="replace")


class DockerRuntime:
    def __init__(self, workspace: Path, image: str, base_image: str, mounts: list[Path],
                 read_only_mounts: list[Path] | None = None, base_network: str = "bridge"):
        self.workspace = workspace
        self.image, self.base_image = image, base_image
        self.base_network = base_network
        suffix = uuid.uuid4().hex[:16]
        self.name, self.base_name = f"ae-godot-{suffix}", f"ae-game-base-{suffix}"
        self.mounts = list(dict.fromkeys([workspace, *mounts]))
        self.read_only_mounts = list(dict.fromkeys(read_only_mounts or []))
        self.queue = asyncio.Queue()
        self.task = None
        self.ready = None
        self.base_ready = False
        self.schemas = {}
        self.lock = asyncio.Lock()

    def mount_args(self, paths, read_only=False):
        from agentevolver.sandbox.default.base import to_host_path

        result = []
        for path in paths:
            if not read_only:
                path.mkdir(parents=True, exist_ok=True)
            source = to_host_path(str(path))
            if "," in source or "," in str(path):
                raise ValueError("Docker bind paths cannot contain commas")
            # Preserve canonical paths: plan and log links mean the same thing in
            # the host, base container and engine container.
            result += ["--mount", f"type=bind,source={source},target={path}" + (",readonly" if read_only else "")]
        return result

    async def prepare_base(self):
        if self.base_ready:
            return
        # Reuse Model X's base container when the framework is already inside it.
        # Host runs get a minimal authoring container with Bash and Python.
        if os.environ.get("AGENTEVOLVER_HOST_ROOT"):
            self.base_ready = True
            return
        if os.environ.get("AGENTEVOLVER_EXEC_CONTAINER"):
            raise ValueError("An external Bash container is already selected; use the owned game base or Model X")
        await docker_command("image", "inspect", self.base_image)
        try:
            await docker_command(
                "run", "-d", "--pull=never", "--name", self.base_name, "--init",
                "--label", "agentevolver.role=game-base", "--network", self.base_network,
                "--user", f"{os.getuid()}:{os.getgid()}",
                *self.mount_args(self.mounts), *self.mount_args(self.read_only_mounts, read_only=True),
                "--workdir", str(self.workspace),
                self.base_image, "sleep", "infinity",
            )
            bind_container(self.workspace, self.base_name)
            self.base_ready = True
        except BaseException:
            await self.remove(self.base_name)
            raise

    async def start(self):
        await self.prepare_base()
        if self.task is not None:
            if self.task.done():
                raise RuntimeError("Godot MCP connection exited; close this session and retry")
            await asyncio.shield(self.ready)
            return
        await docker_command("image", "inspect", self.image)
        self.ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._serve(), name=self.name)
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), 60)
        except BaseException:
            await self.close(remove_base=False)
            raise

    async def _serve(self):
        params = StdioServerParameters(
            command=os.environ.get("AGENTEVOLVER_DOCKER", "docker"),
            env={key: os.environ[key] for key in (
                "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_CONFIG"
            ) if key in os.environ},
            args=["run", "--rm", "-i", "--pull=never", "--name", self.name,
                  "--init", "--label", "agentevolver.role=godot", "--network", "none",
                  "--shm-size", "256m", "--user", f"{os.getuid()}:{os.getgid()}",
                  "--env", f"GODOT_MCP_ALLOWED_DIRS={self.workspace}",
                  "--env", f"XDG_DATA_HOME={self.workspace / '.godot-agent' / 'userdata'}",
                  *self.mount_args([self.workspace]), "--workdir", str(self.workspace), self.image],
        )
        current = None
        log_dir = self.workspace / ".godot-agent"
        log_dir.mkdir(exist_ok=True)
        try:
            with (log_dir / "mcp-stderr.log").open("a") as stderr:
                async with stdio_client(params, errlog=stderr) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        catalog = await client.list_tools()
                        self.schemas = {tool.name: tool.inputSchema for tool in catalog.tools}
                        self.ready.set_result(True)
                        while True:
                            request = await self.queue.get()
                            if request is None:
                                break
                            tool, arguments, current = request
                            result = await client.call_tool(tool, arguments)
                            if not current.done():
                                current.set_result(result)
                            current = None
        except BaseException as error:
            failure = RuntimeError(f"Godot MCP connection failed: {error}; see {log_dir / 'mcp-stderr.log'}")
            if not self.ready.done():
                self.ready.set_exception(failure)
            if current is not None and not current.done():
                current.set_exception(failure)
        finally:
            while not self.queue.empty():
                request = self.queue.get_nowait()
                if request and not request[2].done():
                    request[2].set_exception(RuntimeError("Godot MCP connection closed"))

    async def call(self, tool: str, arguments: dict, timeout: float = 60):
        async with self.lock:
            await self.start()
            if tool not in self.schemas:
                raise ValueError(f"Tool unavailable in pinned MCP server: {tool}")
            import jsonschema

            jsonschema.validate(arguments, self.schemas[tool])
            future = asyncio.get_running_loop().create_future()
            await self.queue.put((tool, arguments, future))
            try:
                return await asyncio.wait_for(future, timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # An interrupted input command must not leave keys/processes held.
                await asyncio.shield(self.close(remove_base=False))
                raise

    @staticmethod
    async def remove(name):
        # Names are randomly generated by this owner, never caller-supplied.
        try:
            await docker_command("rm", "-f", name)
        except RuntimeError as error:
            if "No such container" not in str(error):
                raise

    async def close(self, remove_base=True):
        if remove_base:
            unbind_container(self.workspace, self.base_name)
        if self.task is not None:
            if not self.task.done():
                await self.queue.put(None)
                try:
                    await asyncio.wait_for(asyncio.shield(self.task), 8)
                except asyncio.TimeoutError:
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.remove(self.name)
        if remove_base:
            await self.remove(self.base_name)
            self.base_ready = False
