"""Science manager: one JupyterLab workstation container per project.

Lifecycle mirrors the IDE manager — lazy start, heartbeat while the view is on
screen, idle reaping — because the constraint is the same: the gateway has no
``session.close``, so time, not teardown, is what frees these.

What differs is the container. This one is launched straight through Docker so
it can be given ``--gpus``; opensandbox has no device option, and a workstation
that cannot reach a GPU is not a workstation.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.sandbox.types import SandboxConfig
from agentevolver.science.types import ComputeStatus, Notebook, ScienceInstance

#: Container paths the entrypoint reads. Mirrored in docker/science/.
CONTAINER_WORKSPACE = "/workspace"
#: $HOME inside the container. Mounted per owner so pip installs, wandb logins
#: and Jupyter's own settings survive the container being reaped.
CONTAINER_HOME = "/home/science"


def base_path(session_id: str) -> str:
    """Sub-path this project's Lab is served under, on the UI's own origin.

    JupyterLab is started with this as ``--ServerApp.base_url``, so every
    absolute URL it emits already carries the prefix and the UI can host the Lab
    at ``<whatever origin the browser used>/science/<session>/``. The same
    reasoning as the Code view: a per-session hostname is resolved by the
    BROWSER, so it only ever works when the browser runs on the server.
    """
    return f"/science/{session_id}"


class ScienceManagerServer:
    """Start, track, and reap per-project JupyterLab workstations."""

    #: Reap a container after this long with no heartbeat or proxied request.
    #: Longer than the IDE's: a training run can hold the tab in the background
    #: for a while, and killing the container kills the run with it.
    idle_timeout_seconds: float = 7200.0
    reap_interval_seconds: float = 120.0
    #: Workstations are expensive (GPUs, memory), so fewer than IDEs.
    max_instances: int = 2
    #: How long to wait for JupyterLab to accept connections.
    ready_timeout_seconds: float = 300.0

    def __init__(self) -> None:
        self._instances: Dict[str, ScienceInstance] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._reaper: Optional[asyncio.Task] = None

    # ----------------------------------------------------------- lifecycle
    async def start(self, session_id: str, *, workspace_root: str | Path,
                    owner: str = "local") -> ScienceInstance:
        """Return the running workstation for ``session_id``, starting one if needed."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            existing = self._instances.get(session_id)
            if existing is not None:
                existing.last_seen = time.time()
                return existing

            await self._evict_if_full()

            workspace = Path(workspace_root).expanduser().resolve()
            home = path_manager.get(P.SCIENCE_HOME, owner=owner)
            notebooks = path_manager.get(P.SESSION_NOTEBOOKS, owner=owner, session_id=session_id)
            for directory in (workspace, home, notebooks):
                directory.mkdir(parents=True, exist_ok=True)

            from agentevolver.sandbox.default.science import ScienceSandbox

            sandbox = ScienceSandbox(SandboxConfig(
                # Notebooks install packages and pull datasets.
                network=True,
                entrypoint=["/usr/local/bin/entrypoint-science"],
                env={
                    "SCIENCE_BASE_URL": base_path(session_id) + "/",
                    "SCIENCE_WORKSPACE": CONTAINER_WORKSPACE,
                    "HOME": CONTAINER_HOME,
                },
                mounts={
                    str(workspace): CONTAINER_WORKSPACE,
                    str(home): CONTAINER_HOME,
                },
            ))
            logger.info(f"| 🔬 Science workstation starting for {session_id} ({workspace})")
            await sandbox.start()
            upstream = await sandbox.lab_url()

            instance = ScienceInstance(
                session_id=session_id, owner=owner, upstream=upstream,
                base_path=base_path(session_id), workspace_root=str(workspace),
                sandbox=sandbox, gpus=sandbox._requested_gpus() or "",  # noqa: SLF001
            )
            self._instances[session_id] = instance
            self._ensure_reaper()

            if not await self._wait_ready(upstream + instance.base_path):
                logger.warning(f"| ⚠️ JupyterLab for {session_id} did not answer in time; serving anyway")
            logger.info(f"| ✅ Science workstation ready for {session_id}")
            return instance

    async def stop(self, session_id: str) -> bool:
        """Destroy the project's workstation. True if one was running."""
        instance = self._instances.pop(session_id, None)
        if instance is None:
            return False
        logger.info(f"| ⚫ Science workstation stopping for {session_id}")
        try:
            if instance.sandbox is not None:
                await asyncio.wait_for(instance.sandbox.destroy(), timeout=30)
        except Exception as exc:  # noqa: BLE001 — teardown must not raise
            logger.warning(f"| ⚠️ Science teardown failed for {session_id}: {exc}")
        return True

    async def stop_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for session_id in list(self._instances):
            await self.stop(session_id)

    # -------------------------------------------------------------- lookup
    def upstream(self, session_id: str) -> Optional[str]:
        """Proxy target, refreshing the idle clock so an open Lab stays alive."""
        instance = self._instances.get(session_id)
        if instance is None:
            return None
        instance.last_seen = time.time()
        return instance.upstream

    def touch(self, session_id: str) -> bool:
        instance = self._instances.get(session_id)
        if instance is None:
            return False
        instance.last_seen = time.time()
        return True

    def status(self, session_id: str) -> dict:
        instance = self._instances.get(session_id)
        return instance.public() if instance else {"session_id": session_id, "running": False}

    # ------------------------------------------------------------- compute
    async def compute(self, session_id: str) -> ComputeStatus:
        """What the workstation is running on — GPUs, CPU, memory, disk.

        Everything is read from *inside* the container: the point is what this
        workstation can use, not what the host happens to own.
        """
        instance = self._instances.get(session_id)
        if instance is None or instance.sandbox is None:
            return ComputeStatus(running=False)

        gpus = await instance.sandbox.gpu_status()
        probe = await instance.sandbox.run_command(
            "python -c \"import json,os,shutil;"
            "mem=dict(l.split(':') for l in open('/proc/meminfo').read().strip().split(chr(10)));"
            "total=int(mem['MemTotal'].split()[0]);avail=int(mem['MemAvailable'].split()[0]);"
            "d=shutil.disk_usage('/workspace');"
            "print(json.dumps({'cpu':os.cpu_count(),'total':total//1024,"
            "'used':(total-avail)//1024,'disk':d.free//(1024*1024)}))\"", timeout=20)
        stats = {}
        if probe.success:
            try:
                stats = json.loads(probe.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                logger.warning(f"| ⚠️ Could not parse compute stats for {session_id}")

        return ComputeStatus(
            running=True, gpus=gpus,
            cpu_count=stats.get("cpu"),
            memory_total_mb=stats.get("total"), memory_used_mb=stats.get("used"),
            disk_free_mb=stats.get("disk"),
            uptime_seconds=round(time.time() - instance.started_at, 1),
        )

    # ----------------------------------------------------------- notebooks
    @staticmethod
    def notebooks(session_id: str, *, owner: str = "local") -> List[Notebook]:
        """Every ``.ipynb`` in the project's workspace, newest first.

        Read off disk rather than from the container, so the list is there
        before the workstation has been started — and stays there after it is
        reaped. The notebook is a workspace file; the container is not.
        """
        workspace = path_manager.get(P.SESSION_WORKSPACE, owner=owner, session_id=session_id)
        if not workspace.is_dir():
            return []
        found: List[Notebook] = []
        for path in workspace.rglob("*.ipynb"):
            if ".ipynb_checkpoints" in path.parts:
                continue
            try:
                stat = path.stat()
                cells = len(json.loads(path.read_text(encoding="utf-8")).get("cells", []))
            except (OSError, ValueError):
                continue  # unreadable or mid-write; it will appear next refresh
            found.append(Notebook(
                path=str(path.relative_to(workspace)), title=path.stem,
                size_bytes=stat.st_size, cell_count=cells,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            ))
        return sorted(found, key=lambda item: item.modified_at, reverse=True)

    @staticmethod
    def create_notebook(session_id: str, name: str, *, owner: str = "local") -> Notebook:
        """Write an empty notebook into the project's ``notebooks/`` directory."""
        directory = path_manager.get(P.SESSION_NOTEBOOKS, owner=owner, session_id=session_id, create=True)
        stem = "".join(char for char in (name or "untitled") if char.isalnum() or char in " -_").strip() or "untitled"
        path = directory / f"{stem}.ipynb"
        counter = 2
        while path.exists():
            path = directory / f"{stem}-{counter}.ipynb"
            counter += 1
        # nbformat 4.5's minimum viable document. Written as JSON rather than
        # through nbformat so this works in the gateway's environment, which
        # does not carry the science image's dependencies.
        path.write_text(json.dumps({
            "cells": [], "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            }, "nbformat": 4, "nbformat_minor": 5,
        }, indent=1), encoding="utf-8")
        workspace = path_manager.get(P.SESSION_WORKSPACE, owner=owner, session_id=session_id)
        return Notebook(path=str(path.relative_to(workspace)), title=path.stem,
                        size_bytes=path.stat().st_size, cell_count=0,
                        modified_at=datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------ internals
    async def _wait_ready(self, upstream: str) -> bool:
        """Poll until JupyterLab answers, so the iframe never races it."""
        import aiohttp

        deadline = time.time() + self.ready_timeout_seconds
        async with aiohttp.ClientSession() as session:
            while time.time() < deadline:
                try:
                    async with session.get(f"{upstream}/lab", timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status < 500:
                            return True
                except Exception:  # noqa: BLE001 — not up yet
                    pass
                await asyncio.sleep(1.0)
        return False

    async def _evict_if_full(self) -> None:
        while len(self._instances) >= self.max_instances:
            oldest = min(self._instances.values(), key=lambda item: item.last_seen)
            logger.info(f"| ♻️ Science cap reached; evicting least-recently-used {oldest.session_id}")
            await self.stop(oldest.session_id)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop(), name="science-reaper")

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reap_interval_seconds)
            cutoff = time.time() - self.idle_timeout_seconds
            for session_id, instance in list(self._instances.items()):
                if instance.last_seen < cutoff:
                    logger.info(f"| ⏲️ Science idle past {self.idle_timeout_seconds:.0f}s; reaping {session_id}")
                    await self.stop(session_id)


# Global science manager instance
science_manager = ScienceManagerServer()

__all__ = ["ScienceManagerServer", "science_manager", "base_path"]
