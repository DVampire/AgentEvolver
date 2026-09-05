"""Crash-safe ledger of opensandbox containers started by this framework.

Peer containers (chrome-vnc, playwright, code-interpreter) outlive a crashed
or killed gateway process: nothing destroys them, and the orphans then break
every subsequent boot (observed live: leaked chrome-vnc sandboxes made each
new browser-environment start fail with "Network connectivity error", which
silently emptied the environments capability list).

The fix is a write-ahead ledger: a sandbox id is recorded right after its
container is created and forgotten on clean destroy. Whatever is still in the
ledger when a new process boots belongs to a dead run — ``reap_stale`` force-
removes those containers before the first sandbox of the new run is created.

Assumes one framework instance per tree root (the same assumption
the port registry and deploy registry already make).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List

from agentevolver.paths import P, path_manager
from agentevolver.utils.file_utils import atomic_json_update
from agentevolver.logger import logger


class Ledger:
    """A write-ahead ledger of live sandbox container ids, persisted to the
    tree root. Reaping removes whatever a dead run left behind."""

    DOCKER_SOCKET = "/var/run/docker.sock"

    def _path(self) -> Path:
        from agentevolver.utils.path_utils import home_dir
        return path_manager.get(P.LEDGER, create=True)

    @staticmethod
    def _clean(data) -> dict:
        if isinstance(data, list):
            # Old entries have no provable owner. Keep them for explicit cleanup;
            # absence of ownership is NOT evidence that a container is orphaned.
            return {str(item): None for item in data}
        if isinstance(data, dict):
            return dict(data)
        raise ValueError("Malformed sandbox ledger")

    def _load(self) -> dict:
        try:
            return self._clean(json.loads(self._path().read_text(encoding="utf-8")))
        except FileNotFoundError:
            return {}

    @staticmethod
    def _owner() -> dict:
        import psutil

        return {"pid": os.getpid(), "created": psutil.Process().create_time()}

    @staticmethod
    def _dead(owner) -> bool:
        import psutil

        if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int):
            return False
        try:
            return psutil.Process(owner["pid"]).create_time() != owner["created"]
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, KeyError):
            return False

    def record(self, sandbox_id: str) -> None:
        """Note a live sandbox container (call right after successful create).

        Read-modify-write under a cross-process lock: a second framework instance —
        concurrent ProgramBench runs are exactly this — must not read the same ledger,
        add its own id, and write back over the first instance's, which would leak a
        container the reaper can no longer see.
        """
        if not sandbox_id:
            return
        owner = self._owner()

        def add(data):
            entries = self._clean(data)
            if sandbox_id in entries and entries[sandbox_id] != owner:
                raise RuntimeError(f"Sandbox {sandbox_id} already has a different or unknown owner")
            entries[str(sandbox_id)] = owner
            return entries

        atomic_json_update(self._path(), add, default={}, recover_corrupt=False)

    def forget(self, sandbox_id: str) -> None:
        """Drop a sandbox from the ledger (call after clean destroy)."""
        if not sandbox_id:
            return
        atomic_json_update(
            self._path(),
            lambda ids: {i: owner for i, owner in self._clean(ids).items() if i != str(sandbox_id)},
            default={}, recover_corrupt=False)

    def stale_ids(self) -> List[str]:
        return sorted(self._load())

    async def _remove_container(self, name: str) -> bool:
        """Force-remove one container; True when it is gone (removed or absent).

        The framework container has the Docker socket mounted but no docker CLI,
        so the Engine API over the unix socket is the primary path; the CLI is a
        fallback for host runs without a reachable socket.
        """
        if os.path.exists(self.DOCKER_SOCKET):
            import httpx
            transport = httpx.AsyncHTTPTransport(uds=self.DOCKER_SOCKET)
            async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=20.0) as client:
                response = await client.delete(f"/containers/{name}", params={"force": "true"})
                return response.status_code in (204, 404)
        process = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        return await process.wait() == 0

    async def reap_stale(self) -> List[str]:
        """Force-remove containers recorded by a previous (dead) run.

        Containers are named ``sandbox-<id>`` by the opensandbox daemon. A
        missing container is not an error — the goal is simply that nothing from
        the ledger can squat on resources for the new run. Entries stay in the
        ledger when removal is impossible (e.g. no socket and no CLI) so a later
        boot can retry.
        """
        entries = self._load()
        ids = [key for key, owner in entries.items() if self._dead(owner)]
        if not ids:
            return []
        reaped: List[str] = []
        remaining: List[str] = []
        for sandbox_id in ids:
            name = sandbox_id if sandbox_id.startswith("sandbox-") else f"sandbox-{sandbox_id}"
            try:
                if await self._remove_container(name):
                    reaped.append(name)
                else:
                    remaining.append(sandbox_id)
            except Exception as exc:  # noqa: BLE001 — reaping is best-effort by design
                logger.warning(f"| ⚠️ Sandbox ledger: could not remove {name}: {exc}")
                remaining.append(sandbox_id)
        # Write back as a set difference, not as an overwrite. Reaping runs on boot and
        # takes a while (it awaits a container removal per id); a concurrent instance may
        # have `record`ed a fresh id in the meantime, and replacing the file with
        # `remaining` would erase that live container from the ledger and leak it. So
        # remove only the ids this reap actually cleared, against whatever the file holds
        # now.
        cleared = {i for i in ids if i not in remaining}
        atomic_json_update(
            self._path(),
            lambda current: {i: owner for i, owner in self._clean(current).items()
                             if i not in cleared or owner != entries[i]},
            default={}, recover_corrupt=False)
        if reaped:
            logger.warning(f"| 🧹 Reaped {len(reaped)} stale sandbox container(s) from a previous run: {', '.join(reaped)}")
        return reaped

    async def remove(self, sandbox_id: str) -> bool:
        """Remove one recorded OpenSandbox container by exact id."""
        if not sandbox_id:
            return False
        name = sandbox_id if sandbox_id.startswith("sandbox-") else f"sandbox-{sandbox_id}"
        removed = await self._remove_container(name)
        if removed:
            raw_id = sandbox_id.removeprefix("sandbox-")
            self.forget(raw_id)
            self.forget(name)
        return removed


# One ledger per tree root (same assumption as the port/deploy registries).
ledger = Ledger()

__all__ = ["Ledger", "ledger"]
