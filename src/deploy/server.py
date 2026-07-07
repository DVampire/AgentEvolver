"""Deployment Manager Server.

``deployment_manager`` is the singleton entry point for the deployment subsystem.
It runs one web service per *site* inside an isolated OpenSandbox container and
binds it to a reachable URL, keeping a persisted registry of sites so they can be
listed / re-deployed / stopped.

Design (see ``src/deploy/types.py``): the manager is **framework-agnostic**. It
only knows the generic lifecycle —

    acquire sandbox → upload source → run build → start server (background)
    → expose_port → health-check → record in registry

The per-framework knowledge (image / build / start / health) lives in pluggable
:class:`~src.deploy.types.Deployer` profiles registered under ``DEPLOYER``. Adding
a new deployable target type is "register a new profile", never "edit this file".

Like ``sandbox_manager`` this carries no versioning machinery — a deployment is
infrastructure, not an evolvable component. Site handles live in-process (via
``sandbox_manager``); the JSON registry persists metadata so sites survive a
process restart as ``DETACHED`` records that ``redeploy`` can bring back.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from src.config import config
from src.logger import logger
from src.registry import DEPLOYER
from src.sandbox import sandbox_manager
from src.deploy.types import (
    Deployer,
    DeploymentSpec,
    DeployRequest,
    HealthCheck,
    SiteRecord,
    SiteStatus,
)

# Directories skipped when uploading a host source tree into a container.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", ".cache", "dist", "build", ".DS_Store"}
_SANDBOX_KIND = "opensandbox"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentManagerServer(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._sites: Dict[str, SiteRecord] = {}
        self._registry_path: Optional[str] = None
        self._initialized = False

    # --------------------------------------------------------------- lifecycle
    async def initialize(self, work_dir: Optional[str] = None) -> None:
        """Load the persisted site registry and register built-in profiles. Idempotent."""
        if self._initialized:
            return
        import src.deploy.default  # noqa: F401  (registers built-in profiles with DEPLOYER)

        # Prefer the framework's per-run default dir; fall back to a local path so the
        # subsystem still works if it is exercised before config.initialize() has run.
        default_dir = getattr(config, "default_dir", None) or os.path.join("work_dir", "default")
        base = work_dir or os.path.join(default_dir, "deploy")
        os.makedirs(base, exist_ok=True)
        self._registry_path = os.path.join(base, "sites.json")
        self._load()
        # Sites recorded but with no live handle after a restart are DETACHED.
        for rec in self._sites.values():
            if rec.status == SiteStatus.RUNNING:
                rec.status = SiteStatus.DETACHED
        self._save()
        self._initialized = True
        logger.info(
            f"| 🚀 Deployment manager ready (profiles: {await self.list_profiles()}; "
            f"{len(self._sites)} site(s) in registry)"
        )

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    # --------------------------------------------------------------- discovery
    async def list_profiles(self) -> List[str]:
        import src.deploy.default  # noqa: F401
        return sorted(DEPLOYER.module_dict.keys())

    def _profile(self, runtime: str) -> Deployer:
        cls = DEPLOYER.get(runtime)
        if cls is None:
            raise ValueError(
                f"No deploy profile {runtime!r}. Available: {sorted(DEPLOYER.module_dict.keys())}"
            )
        return cls()

    # --------------------------------------------------------------- registry io
    def _load(self) -> None:
        if self._registry_path and os.path.exists(self._registry_path):
            try:
                with open(self._registry_path) as f:
                    raw = json.load(f)
                self._sites = {sid: SiteRecord(**rec) for sid, rec in raw.items()}
            except Exception as e:
                logger.warning(f"| ⚠️ Could not load deploy registry: {e}")
                self._sites = {}

    def _save(self) -> None:
        if not self._registry_path:
            return
        try:
            with open(self._registry_path, "w") as f:
                json.dump({sid: rec.model_dump() for sid, rec in self._sites.items()}, f, indent=2)
        except Exception as e:
            logger.warning(f"| ⚠️ Could not persist deploy registry: {e}")

    # --------------------------------------------------------------- spec resolution
    def _resolve_spec(self, request: DeployRequest) -> DeploymentSpec:
        """Profile → base spec, then overlay request.port / request.env / request.overrides."""
        spec = self._profile(request.runtime).make_spec(request)
        ov = dict(request.overrides or {})
        for field in ("image", "workdir", "build", "start", "timeout_minutes"):
            if field in ov and ov[field] is not None:
                setattr(spec, field, ov[field])
        if "health" in ov and ov["health"]:
            spec.health = HealthCheck(**ov["health"]) if isinstance(ov["health"], dict) else ov["health"]
        if request.port:
            spec.port = request.port
        if request.env:
            spec.env = {**spec.env, **request.env}
        return spec

    # --------------------------------------------------------------- deploy
    async def deploy(self, request: DeployRequest) -> SiteRecord:
        """Build and start a site in a fresh container, bind it to a URL, and record it."""
        await self._ensure_initialized()
        spec = self._resolve_spec(request)  # raises on bad profile / missing custom.start

        rec = SiteRecord(
            site_id=request.site_id,
            runtime=spec.runtime,
            status=SiteStatus.BUILDING,
            port=spec.port,
            image=spec.image,
            reuse_key=request.site_id,
            created_at=self._sites.get(request.site_id, SiteRecord(site_id=request.site_id, runtime=spec.runtime)).created_at or _now(),
            updated_at=_now(),
            request=request.model_dump(),
        )
        self._sites[request.site_id] = rec
        self._save()

        try:
            sandbox = await sandbox_manager.acquire(
                _SANDBOX_KIND,
                reuse_key=request.site_id,
                image=spec.image,
                env=spec.env,
                timeout_minutes=spec.timeout_minutes,
                network=True,
            )

            # --- upload source ---------------------------------------------------
            if request.git_url:
                res = await sandbox.run_command(f"git clone {shlex.quote(request.git_url)} {shlex.quote(spec.workdir)}")
                if not res.success:
                    raise RuntimeError(f"git clone failed: {res.as_message()}")
            else:
                await sandbox.run_command(f"mkdir -p {shlex.quote(spec.workdir)}")
                if request.source_dir:
                    await self._upload_dir(sandbox, request.source_dir, spec.workdir)

            # --- build (fail-fast) -----------------------------------------------
            for cmd in spec.build:
                res = await sandbox.run_command(cmd, work_dir=spec.workdir, timeout=1800)
                if not res.success:
                    raise RuntimeError(f"build step failed ({cmd!r}): {res.as_message()}")

            # --- start server in the background ----------------------------------
            start_cmd = f"nohup sh -c {shlex.quote(spec.start)} > {shlex.quote(rec.log_path)} 2>&1 &"
            res = await sandbox.run_command(start_cmd, work_dir=spec.workdir)
            if not res.success:
                raise RuntimeError(f"failed to launch start command: {res.as_message()}")

            # --- bind URL + wait until ready -------------------------------------
            url = await sandbox.expose_port(spec.port)
            ready = await self._health(sandbox, spec, url)
            if not ready:
                tail = await self._log_tail(sandbox, rec.log_path)
                raise RuntimeError(f"service did not become healthy within {spec.health.timeout_s}s. Log tail:\n{tail}")

            rec.url = url
            rec.status = SiteStatus.RUNNING
            rec.error = None
            rec.updated_at = _now()
            self._save()
            logger.info(f"| 🌐 Site '{request.site_id}' ({spec.runtime}) deployed at {url}")
            return rec
        except Exception as e:
            rec.status = SiteStatus.FAILED
            rec.error = str(e)
            rec.updated_at = _now()
            self._save()
            logger.error(f"| ❌ Deploy '{request.site_id}' failed: {e}")
            return rec

    async def _upload_dir(self, sandbox, source_dir: str, workdir: str) -> None:
        src_root = os.path.abspath(source_dir)
        if not os.path.isdir(src_root):
            raise RuntimeError(f"source_dir not found: {source_dir}")
        for root, dirs, files in os.walk(src_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                host_path = os.path.join(root, fname)
                rel = os.path.relpath(host_path, src_root)
                dest = f"{workdir}/{rel}".replace(os.sep, "/")
                try:
                    with open(host_path, "rb") as fh:
                        await sandbox.write_file(dest, fh.read())
                except Exception as e:
                    logger.warning(f"| ⚠️ Skipped uploading {rel}: {e}")

    async def _health(self, sandbox, spec: DeploymentSpec, url: str) -> bool:
        """Poll readiness. http → GET the exposed URL from the host (image-agnostic);
        command → run a caller-supplied command in the container; none → ready at once."""
        hc = spec.health
        if hc.type == "none":
            return True
        deadline = asyncio.get_event_loop().time() + hc.timeout_s
        probe_url = url.rstrip("/") + hc.path
        while asyncio.get_event_loop().time() < deadline:
            try:
                if hc.type == "command" and hc.command:
                    res = await sandbox.run_command(hc.command, work_dir=spec.workdir, timeout=15)
                    if res.success:
                        return True
                else:  # http
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(probe_url)
                        if resp.status_code < 500:  # any response = the server is up
                            return True
            except Exception:
                pass
            await asyncio.sleep(hc.interval_s)
        return False

    async def _log_tail(self, sandbox, log_path: str, lines: int = 40) -> str:
        try:
            res = await sandbox.run_command(f"tail -n {lines} {shlex.quote(log_path)}")
            return res.as_message()
        except Exception:
            return "(no log available)"

    # --------------------------------------------------------------- queries / ops
    async def list_sites(self) -> List[SiteRecord]:
        await self._ensure_initialized()
        return list(self._sites.values())

    async def get_site(self, site_id: str) -> Optional[SiteRecord]:
        await self._ensure_initialized()
        return self._sites.get(site_id)

    async def stop_site(self, site_id: str) -> SiteRecord:
        await self._ensure_initialized()
        rec = self._sites.get(site_id)
        if rec is None:
            raise ValueError(f"No such site {site_id!r}")
        await sandbox_manager.release(_SANDBOX_KIND, reuse_key=site_id)
        rec.status = SiteStatus.STOPPED
        rec.url = None
        rec.updated_at = _now()
        self._save()
        logger.info(f"| 🛑 Site '{site_id}' stopped")
        return rec

    async def redeploy(self, site_id: str) -> SiteRecord:
        """Tear down and rebuild a site from its stored request (new URL likely)."""
        await self._ensure_initialized()
        rec = self._sites.get(site_id)
        if rec is None or not rec.request:
            raise ValueError(f"No redeployable request stored for site {site_id!r}")
        await sandbox_manager.release(_SANDBOX_KIND, reuse_key=site_id)
        return await self.deploy(DeployRequest(**rec.request))

    async def cleanup(self) -> None:
        """Stop all live sites (called on global teardown)."""
        for site_id in list(self._sites.keys()):
            try:
                await sandbox_manager.release(_SANDBOX_KIND, reuse_key=site_id)
            except Exception as e:
                logger.warning(f"| ⚠️ Error releasing site '{site_id}': {e}")


# Global deployment manager instance.
deployment_manager = DeploymentManagerServer()
