"""Deployment Manager Server.

``deployment_manager`` is the singleton entry point for the deployment subsystem.
It runs one web service per *site* inside an isolated OpenSandbox container and
binds it to a reachable URL, keeping a persisted registry of sites so they can be
listed / re-deployed / stopped.

Design (see ``agentevolver/deploy/types.py``): the manager is **framework-agnostic**. It
only knows the generic lifecycle —

    acquire sandbox → upload source → run build → start server (background)
    → expose_port → health-check → record in registry

The per-framework knowledge (image / build / start / health) lives in pluggable
:class:`~agentevolver.deploy.types.Deployer` profiles registered under ``DEPLOYER``. Adding
a new deployable target type is "register a new profile", never "edit this file".

Like ``sandbox_manager`` this carries no versioning machinery — a deployment is
infrastructure, not an evolvable component. Site handles live in-process (via
``sandbox_manager``); the JSON registry persists metadata so sites survive a
process restart as ``DETACHED`` records that ``redeploy`` can bring back.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from agentevolver.deploy.types import (
    Deployer,
    DeploymentSpec,
    DeployRequest,
    HealthCheck,
    SiteRecord,
    SiteStatus,
)
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.registry import DEPLOYER
from agentevolver.sandbox import sandbox_manager
from agentevolver.utils.file_utils import atomic_json_update

# Directories skipped when uploading a host source tree into a container.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    ".cache",
    "dist",
    "build",
    ".DS_Store",
}
_SANDBOX_KIND = "opensandbox"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentManagerServer(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._sites: Dict[str, SiteRecord] = {}
        # Site ids created or redeployed by this process. Global teardown must not stop
        # a persisted deployment merely because another run loaded its registry record.
        self._owned_sites: set[str] = set()
        self._registry_path: Optional[str] = None
        self._initialized = False

    # --------------------------------------------------------------- lifecycle
    async def initialize(self, workspace_root: Optional[str] = None) -> None:
        """Load the persisted site registry and register built-in profiles. Idempotent."""
        if self._initialized:
            return
        import agentevolver.deploy.default  # noqa: F401  (registers built-in profiles with DEPLOYER)

        # Deployed sites are project-global and outlive any single session, so the
        # registry lives at ``output/.runtime/deploy`` (``P.DEPLOY``) rather
        # than a per-session log root — this keeps every session and every restart
        # looking at the same set of sites.
        base = workspace_root or str(path_manager.get(P.DEPLOY, create=True))
        os.makedirs(base, exist_ok=True)
        self._registry_path = str(path_manager.resolve_under(base, "sites.json"))
        self._load()
        # After a restart the in-process sandbox handles are gone; re-probe each
        # recorded site so ones still serving are reattached as RUNNING and the
        # rest are marked DETACHED (redeployable).
        await self._reconcile_on_start()
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
        import agentevolver.deploy.default  # noqa: F401

        return sorted(DEPLOYER.module_dict.keys())

    def _profile(self, runtime: str) -> Deployer:
        cls = DEPLOYER.get(runtime)
        if cls is None:
            raise ValueError(
                f"No deploy profile {runtime!r}. Available: {sorted(DEPLOYER.module_dict.keys())}"
            )
        return cls()

    # --------------------------------------------------------------- backend selection
    @staticmethod
    def _container_runtime_available() -> bool:
        """True if a Docker daemon (or remote host) is reachable — i.e. opensandbox can work."""
        if os.environ.get("DOCKER_HOST"):
            return True
        return os.path.exists("/var/run/docker.sock")

    def _backend_kind(self, request: Optional[DeployRequest] = None) -> str:
        """Pick the sandbox backend.

        Precedence: the request's own ``backend`` (a per-deploy choice), then the
        ``DEPLOY_BACKEND`` env, then ``auto``. ``host`` = local, no container
        (lightweight/instant); ``opensandbox`` = isolated Docker container (heavy);
        ``auto`` = opensandbox when a container runtime is available, else host.

        One default rides on this: an inline artifact (``content``/``files`` with no
        source_dir/git_url) is meant to be lightweight, so when nothing forces a
        backend it deploys on the host rather than spinning a container per page.
        """
        choice = ""
        if request is not None and request.backend:
            choice = request.backend.lower().strip()
        if not choice:
            choice = (os.environ.get("DEPLOY_BACKEND") or "").lower().strip()
        if not choice:
            inline = (
                request is not None
                and (request.content or request.files)
                and not (request.source_dir or request.git_url)
            )
            choice = "host" if inline else "auto"
        if choice in ("host", "local"):
            return "host"
        if choice in ("sandbox", "opensandbox", "docker"):
            return "opensandbox"
        return "opensandbox" if self._container_runtime_available() else "host"

    def _host_site_dir(self, site_id: str) -> str:
        base = (
            os.path.dirname(self._registry_path)
            if self._registry_path
            else str(path_manager.get(P.DEPLOY))
        )
        sites = path_manager.resolve_under(base, "sites")
        site = path_manager.resolve_under(sites, site_id)
        return str(path_manager.resolve_under(site, "app"))

    @staticmethod
    def _reserve_host_port(site_id: str, preferred: int) -> int:
        """Register (and allocate) a host port for a host-backend site.

        Goes through the central ``port_manager`` so the binding lands in
        ``ports.json`` and is de-conflicted with everything else the framework
        binds.  Only host-backend sites need this — container backends have their
        own isolated port space.
        """
        from agentevolver.port import port_manager

        return port_manager.register(f"deploy:{site_id}", preferred=preferred, type="host")["port"]

    async def _reconcile_on_start(self) -> None:
        """Re-probe recorded sites at startup to recover ones still serving.

        A site's in-process sandbox handle does not survive a restart, but the
        service itself (a detached host process or a container) often does.  For
        each site that had a URL, probe it: reachable → RUNNING (reattached),
        otherwise DETACHED (its stored request can ``redeploy`` it).
        """
        for rec in self._sites.values():
            if rec.status in (SiteStatus.STOPPED, SiteStatus.FAILED):
                continue
            rec.status = (
                SiteStatus.RUNNING if await self._url_reachable(rec.url) else SiteStatus.DETACHED
            )

    @staticmethod
    async def _url_reachable(url: Optional[str]) -> bool:
        """True if ``url`` answers an HTTP request within a short timeout."""
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
                resp = await client.get(url)
            return resp.status_code < 500
        except Exception:
            return False

    @staticmethod
    def _release_host_port(record: SiteRecord) -> None:
        if record.backend == "host":
            from agentevolver.port import port_manager

            port_manager.unregister(f"deploy:{record.site_id}")

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
            payload = {sid: rec.model_dump() for sid, rec in self._sites.items()}
            atomic_json_update(
                self._registry_path,
                lambda _current: payload,
                default={},
            )
        except Exception as e:
            logger.warning(f"| ⚠️ Could not persist deploy registry: {e}")

    # --------------------------------------------------------------- spec resolution
    def _resolve_spec(self, request: DeployRequest) -> DeploymentSpec:
        """Profile → base spec, then overlay request.port / request.env / request.overrides."""
        spec = self._profile(request.runtime).make_spec(request)
        ov = dict(request.overrides or {})
        for field in ("image", "workspace_root", "build", "start", "timeout_minutes"):
            if field in ov and ov[field] is not None:
                setattr(spec, field, ov[field])
        if "health" in ov and ov["health"]:
            spec.health = (
                HealthCheck(**ov["health"]) if isinstance(ov["health"], dict) else ov["health"]
            )
        if request.port:
            spec.port = request.port
        if request.env:
            spec.env = {**spec.env, **request.env}
        return spec

    def _materialize_inline(self, request: DeployRequest) -> str:
        """Write inline ``content`` / ``files`` to a host staging dir and return it.

        This is the lightweight path: the caller ships the page/app in the request
        instead of pointing at a host tree, and we turn it into a normal source dir so
        the rest of the deploy flow (upload → build → start) is unchanged. ``files``
        (a {relpath: text} map) wins over ``content`` (a single ``filename``); giving
        both merges them, with ``content`` filling in ``filename`` if absent.
        """
        base = (
            os.path.dirname(self._registry_path)
            if self._registry_path
            else str(path_manager.get(P.DEPLOY, create=True))
        )
        staging = str(
            path_manager.resolve_under(path_manager.resolve_under(base, "staging"), request.site_id)
        )
        # A redeploy must not serve stale files from a previous materialization.
        if os.path.isdir(staging):
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

        files = dict(request.files or {})
        if request.content is not None and request.filename not in files:
            files[request.filename] = request.content
        if not files:
            raise ValueError("inline deploy needs non-empty content or files")
        for rel, text in files.items():
            # Keep writes inside the staging dir — reject path escapes from a relpath.
            dest = os.path.normpath(os.path.join(staging, rel))
            if os.path.commonpath((staging, dest)) != staging:
                raise ValueError(f"unsafe inline file path: {rel!r}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text if isinstance(text, str) else str(text))
        return staging

    @staticmethod
    def _source_revision(request: DeployRequest) -> str:
        """Hash authored source so repeated deploys cannot masquerade as iterations."""
        digest = hashlib.sha256()
        if request.content is not None or request.files:
            payload = {
                "filename": request.filename,
                "content": request.content,
                "files": request.files or {},
            }
            digest.update(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            return digest.hexdigest()
        if request.source_dir and os.path.isdir(request.source_dir):
            root = os.path.abspath(request.source_dir)
            for current, dirs, files in os.walk(root):
                dirs[:] = sorted(name for name in dirs if name not in _SKIP_DIRS)
                for name in sorted(files):
                    path = os.path.join(current, name)
                    relative = os.path.relpath(path, root).replace(os.sep, "/")
                    digest.update(relative.encode("utf-8"))
                    digest.update(b"\0")
                    try:
                        with open(path, "rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                    except OSError:
                        digest.update(b"<unreadable>")
                    digest.update(b"\0")
            return digest.hexdigest()
        if request.git_url:
            digest.update(request.git_url.encode("utf-8"))
            return digest.hexdigest()
        return ""

    def source_revision(self, request: DeployRequest) -> str:
        """Public, side-effect-free source identity used by release gates."""
        return self._source_revision(request)

    # --------------------------------------------------------------- deploy
    async def deploy(self, request: DeployRequest) -> SiteRecord:
        """Build and start a site in a fresh container, bind it to a URL, and record it."""
        await self._ensure_initialized()

        # ``site_id`` is documented as a stable reuse key. Re-deploying that key must
        # replace the old process; starting a second server beside it either leaks the
        # first process or reports the old artifact healthy on the reused port.
        previous = self._sites.get(request.site_id)
        if previous is not None and previous.status not in {
            SiteStatus.STOPPED,
            SiteStatus.FAILED,
        }:
            await self.stop_site(request.site_id)

        # Decide the backend *before* materializing inline content — materialization sets
        # source_dir, which would otherwise mask the "inline ⇒ host by default" rule.
        backend = self._backend_kind(request)

        # Lightweight path: turn inline content/files into a source_dir the normal flow
        # can upload. git_url and an explicit source_dir take precedence and skip this.
        if (request.content or request.files) and not (request.source_dir or request.git_url):
            request.source_dir = self._materialize_inline(request)

        spec = self._resolve_spec(request)  # raises on bad profile / missing custom.start
        source_revision = self._source_revision(request)

        # The host backend has no container filesystem, so run in a real host directory
        # and write the server log beside it (containers keep a per-container /tmp log).
        if backend == "host":
            spec.workspace_root = self._host_site_dir(request.site_id)
            log_path = str(
                path_manager.resolve_under(
                    os.path.dirname(spec.workspace_root),
                    "server.log",
                )
            )
            # Host sites share the machine's ports: if the requested one is taken, move to
            # a free port and reflect it in the start command (literal port) and PORT env.
            free = self._reserve_host_port(request.site_id, spec.port)
            if free != spec.port:
                logger.info(
                    f"| 🖥️  port {spec.port} busy → using free port {free} for '{request.site_id}'"
                )
                spec.start = spec.start.replace(str(spec.port), str(free))
                spec.port = free
        else:
            log_path = "/tmp/deploy_site.log"

        # Expose the chosen port as $PORT so start commands using it (e.g. custom
        # `... --port $PORT`) resolve, regardless of backend.
        spec.env = {**spec.env, "PORT": str(spec.port)}

        rec = SiteRecord(
            site_id=request.site_id,
            runtime=spec.runtime,
            status=SiteStatus.BUILDING,
            port=spec.port,
            image=spec.image,
            backend=backend,
            reuse_key=request.site_id,
            source_revision=source_revision,
            created_at=self._sites.get(
                request.site_id, SiteRecord(site_id=request.site_id, runtime=spec.runtime)
            ).created_at
            or _now(),
            updated_at=_now(),
            log_path=log_path,
            request=request.model_dump(),
        )
        self._sites[request.site_id] = rec
        self._owned_sites.add(request.site_id)
        self._save()

        try:
            if backend == "host":
                sandbox = await sandbox_manager.acquire(
                    "host",
                    reuse_key=request.site_id,
                    env=spec.env,
                    host_base=os.path.dirname(os.path.dirname(spec.workspace_root)),
                )
                logger.info(
                    f"| 🖥️  '{request.site_id}': no container runtime → deploying on HOST (no isolation)"
                )
            else:
                sandbox = await sandbox_manager.acquire(
                    _SANDBOX_KIND,
                    reuse_key=request.site_id,
                    image=spec.image,
                    env=spec.env,
                    timeout_minutes=spec.timeout_minutes,
                    network=True,
                )
            rec.resource_id = sandbox.resource_id
            rec.updated_at = _now()
            self._save()

            # --- upload source ---------------------------------------------------
            if request.git_url:
                res = await sandbox.run_command(
                    f"git clone {shlex.quote(request.git_url)} {shlex.quote(spec.workspace_root)}"
                )
                if not res.success:
                    raise RuntimeError(f"git clone failed: {res.as_message()}")
            else:
                await sandbox.run_command(f"mkdir -p {shlex.quote(spec.workspace_root)}")
                if request.source_dir:
                    await self._upload_dir(sandbox, request.source_dir, spec.workspace_root)

            # --- build (fail-fast) -----------------------------------------------
            for cmd in spec.build:
                res = await sandbox.run_command(
                    cmd, workspace_root=spec.workspace_root, timeout=1800
                )
                if not res.success:
                    raise RuntimeError(f"build step failed ({cmd!r}): {res.as_message()}")

            # --- start server in the background ----------------------------------
            start_cmd = (
                f"nohup sh -c {shlex.quote(spec.start)} > {shlex.quote(rec.log_path)} 2>&1 &"
            )
            res = await sandbox.run_command(start_cmd, workspace_root=spec.workspace_root)
            if not res.success:
                raise RuntimeError(f"failed to launch start command: {res.as_message()}")
            # Host process identity exists only after the background server starts.
            rec.resource_id = sandbox.resource_id
            rec.updated_at = _now()
            self._save()

            # --- bind URL + wait until ready -------------------------------------
            url = await sandbox.expose_port(spec.port)
            ready = await self._health(sandbox, spec, url)
            if not ready:
                tail = await self._log_tail(sandbox, rec.log_path)
                raise RuntimeError(
                    f"service did not become healthy within {spec.health.timeout_s}s. Log tail:\n{tail}"
                )

            rec.url = url
            rec.status = SiteStatus.RUNNING
            rec.error = None
            rec.updated_at = _now()
            self._save()
            logger.info(f"| 🌐 Site '{request.site_id}' ({spec.runtime}) deployed at {url}")
            return rec
        except Exception as e:
            try:
                released = await sandbox_manager.release(
                    backend,
                    reuse_key=request.site_id,
                    resource_id=rec.resource_id,
                )
                if released:
                    rec.resource_id = None
                if released or rec.resource_id is None:
                    self._owned_sites.discard(request.site_id)
                    self._release_host_port(rec)
            except Exception as cleanup_error:  # noqa: BLE001
                logger.warning(f"| ⚠️ Deploy '{request.site_id}' failure cleanup: {cleanup_error}")
            rec.status = SiteStatus.FAILED
            rec.error = str(e)
            rec.updated_at = _now()
            self._save()
            logger.error(f"| ❌ Deploy '{request.site_id}' failed: {e}")
            return rec

    async def _upload_dir(self, sandbox, source_dir: str, workspace_root: str) -> None:
        src_root = os.path.abspath(source_dir)
        if not os.path.isdir(src_root):
            raise RuntimeError(f"source_dir not found: {source_dir}")
        for root, dirs, files in os.walk(src_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                host_path = os.path.join(root, fname)
                rel = os.path.relpath(host_path, src_root)
                dest = f"{workspace_root}/{rel}".replace(os.sep, "/")
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
        # If the backend can tell us our launched server has died (e.g. failed to bind
        # its port), stop immediately — otherwise a stale/other server on the same port
        # could answer the probe and produce a false "healthy".
        alive_check = getattr(sandbox, "launched_alive", None)
        deadline = asyncio.get_event_loop().time() + hc.timeout_s
        probe_url = url.rstrip("/") + hc.path
        while asyncio.get_event_loop().time() < deadline:
            if alive_check is not None and not alive_check():
                logger.warning("| ⚠️ deploy: launched server process exited before becoming healthy")
                return False
            try:
                if hc.type == "command" and hc.command:
                    res = await sandbox.run_command(
                        hc.command, workspace_root=spec.workspace_root, timeout=15
                    )
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

    # -- name-addressed sites -------------------------------------------------

    def resolve_port(self, name: str) -> Optional[int]:
        """The port a site name answers on right now, or None.

        Deliberately synchronous and lock-free: it is called per HTTP request by the
        gateway's relay, and it reads one dict.

        A site's PORT changes on every redeploy — the deployer asks for a free one — so
        an address built from a port dies with the release that minted it. Every
        participant in the website scenario is asked to come back to an ark they visited
        before, and every one of them was handed a different URL each round instead. The
        name is the stable identity; the port is an implementation detail behind it.

        `<site>--r<n>` addresses one exact release, which is what an independent
        acceptance worker needs: a verdict on "the current site" is not a verdict on the
        release it was asked about.
        """
        record = self._sites.get(name)
        if record is None and "--r" in name:
            base, _, suffix = name.rpartition("--r")
            if suffix.isdigit():
                record = self._sites.get(base)
                if record is not None and str(
                    getattr(record, "release_number", "")
                ) != suffix:
                    return None
        if record is None or record.status is not SiteStatus.RUNNING:
            return None
        return int(record.port) if record.port else None

    def public_names(self) -> List[str]:
        """Every name that currently resolves, for diagnostics and the 404 body."""
        return sorted(
            name for name, rec in self._sites.items()
            if rec.status is SiteStatus.RUNNING and rec.port
        )

    async def stop_site(self, site_id: str) -> SiteRecord:
        await self._ensure_initialized()
        rec = self._sites.get(site_id)
        if rec is None:
            raise ValueError(f"No such site {site_id!r}")
        stopped = await sandbox_manager.release(
            rec.backend or _SANDBOX_KIND,
            reuse_key=site_id,
            resource_id=rec.resource_id,
        )
        if not stopped and (rec.resource_id or await self._url_reachable(rec.url)):
            raise RuntimeError(
                f"Site {site_id!r} still has a live or unverified backend identity; "
                "refusing to report a false stop"
            )
        rec.status = SiteStatus.STOPPED
        rec.url = None
        rec.resource_id = None
        self._owned_sites.discard(site_id)
        self._release_host_port(rec)
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
        request = DeployRequest(**rec.request)
        await self.stop_site(site_id)
        return await self.deploy(request)

    async def cleanup(self) -> None:
        """Stop only sites acquired by this process (called on global teardown)."""
        for site_id in list(self._owned_sites):
            rec = self._sites.get(site_id)
            if rec is None:
                self._owned_sites.discard(site_id)
                continue
            try:
                released = await sandbox_manager.release(
                    rec.backend or _SANDBOX_KIND,
                    reuse_key=site_id,
                    resource_id=rec.resource_id,
                )
                if released:
                    rec.status = SiteStatus.STOPPED
                    rec.url = None
                    rec.resource_id = None
                    rec.updated_at = _now()
                    self._owned_sites.discard(site_id)
                    self._release_host_port(rec)
            except Exception as e:
                logger.warning(f"| ⚠️ Error releasing site '{site_id}': {e}")
        self._save()


# Global deployment manager instance.
deployment_manager = DeploymentManagerServer()
