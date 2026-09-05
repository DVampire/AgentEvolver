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
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

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

# How long a lazily-started older release keeps running after its last request. Long
# enough to read a page and click through it; short enough that comparing six releases
# does not leave six servers behind.
_RELEASE_IDLE_S = 900.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentManagerServer(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._sites: Dict[str, SiteRecord] = {}
        # Last time each on-demand release was asked for, so idle ones can be reclaimed.
        self._release_seen: Dict[str, float] = {}
        self._release_locks: Dict[str, asyncio.Lock] = {}
        # Site ids created or redeployed by this process. Global teardown must not stop
        # a persisted deployment merely because another run loaded its registry record.
        self._owned_sites: set[str] = set()
        self._registry_path: Optional[str] = None
        self._initialized = False
        self._saved_sites = {}
        self._registry_stamp = None

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

    def _backend_kind(
        self,
        request: Optional[DeployRequest] = None,
        previous: Optional[SiteRecord] = None,
    ) -> str:
        """Pick the sandbox backend.

        Precedence: the request's own ``backend`` (a per-deploy choice), then the backend
        this ``site_id`` is already running on, then the ``DEPLOY_BACKEND`` env, then the
        source's default. ``host`` = local, no container (lightweight/instant);
        ``opensandbox`` = isolated Docker container (heavy); ``auto`` = opensandbox when a
        container runtime is available, else host.

        A site keeps the substrate it was born on. ``site_id`` is a stable identity, and a
        redeploy that silently moved between host and container changed the URL's shape
        under whoever was already holding it and discarded whatever the server had written
        since — for one live site that meant six releases split across two substrates
        because a single optional argument stopped being passed. Moving is still possible,
        but it now takes saying so.

        The source decides the rest. Anything local — inline ``content``/``files``, or a
        ``source_dir`` this agent just wrote in its own workspace — deploys on the host: a
        container cannot isolate the machine from code the agent is already running
        unsandboxed beside it, so the isolation would be nominal while the costs are real
        (an opaque proxy URL, a build per deploy, a filesystem that resets each time). A
        ``git_url`` is the genuinely different case: foreign code arriving over the
        network, where the container earns its keep. That one still defaults to ``auto``.
        """
        choice = ""
        if request is not None and request.backend:
            choice = request.backend.lower().strip()
        if not choice and previous is not None and previous.backend:
            choice = previous.backend.lower().strip()
        if not choice:
            choice = (os.environ.get("DEPLOY_BACKEND") or "").lower().strip()
        if not choice:
            foreign = request is not None and bool(request.git_url)
            choice = "auto" if foreign else "host"
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

    def _release_dir(self, site_id: str, release: int) -> str:
        """Where release ``n`` of ``site_id`` keeps its own copy of the source.

        A release has to be a thing that exists, not just a number. The agent edits its
        workspace in place, the staging tree is wiped on every redeploy, and the registry
        holds one record per site — so before this, the moment release n+1 landed, every
        earlier release's bytes were gone from the machine entirely. `--r<n>` could only
        ever have answered for whatever was current.
        """
        base = (
            os.path.dirname(self._registry_path)
            if self._registry_path
            else str(path_manager.get(P.DEPLOY))
        )
        sites = path_manager.resolve_under(base, "sites")
        site = path_manager.resolve_under(sites, site_id)
        releases = path_manager.resolve_under(site, "releases")
        return str(path_manager.resolve_under(releases, f"r{int(release)}"))

    def _archive_release(self, site_id: str, release: int, source_dir: str) -> str:
        """Create an immutable source snapshot; never overwrite an earlier version."""
        import shutil
        import tempfile

        destination = self._release_dir(site_id, release)
        if os.path.isdir(destination):
            archived = self._source_revision(DeployRequest(site_id=site_id, source_dir=destination))
            incoming = self._source_revision(DeployRequest(site_id=site_id, source_dir=source_dir))
            if archived != incoming:
                raise ValueError(f"Refusing to overwrite archived {site_id} r{release}")
            return destination
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        temporary = tempfile.mkdtemp(prefix=".snapshot-", dir=os.path.dirname(destination))
        try:
            shutil.copytree(
                source_dir,
                temporary,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*_SKIP_DIRS),
            )
            os.rename(temporary, destination)
        finally:
            if os.path.isdir(temporary):
                shutil.rmtree(temporary)
        return destination

    def _version_metadata(self, site_id: str, release: int) -> str:
        archive = self._release_dir(site_id, release)
        return str(path_manager.resolve_under(os.path.dirname(archive), f"r{int(release)}.json"))

    def _record_version(self, record: SiteRecord) -> None:
        """Pin the original recipe beside its source, outside the served directory."""
        if self._split_release(record.site_id):
            return
        archive = self._release_dir(record.site_id, record.release_number)
        if not os.path.isdir(archive):
            return
        if any(v["number"] == record.release_number for v in record.versions):
            return
        entry = dict(number=record.release_number, source_revision=record.source_revision,
                     deployed_at=record.deployed_at, runtime=record.runtime,
                     owner_session_id=record.request.get("owner_session_id"),
                     stage=record.request.get("stage", "published"),
                     url=f"/s/{quote(record.site_id, safe='')}--r{record.release_number}/")
        atomic_json_update(self._version_metadata(record.site_id, record.release_number),
                           lambda existing: existing or {**entry, "request": record.request})
        record.versions.append(entry)
        record.versions = self.version_history(record)

    def version_history(self, record: SiteRecord) -> List[Dict[str, Any]]:
        """Include legacy source archives without inventing deployment timestamps."""
        versions = {v["number"]: v for v in record.versions}
        root = os.path.dirname(self._release_dir(record.site_id, 1))
        if os.path.isdir(root):
            for name in os.listdir(root):
                if not name.startswith("r") or not name[1:].isdigit():
                    continue
                number = int(name[1:])
                if number in versions or not os.path.isdir(self._release_dir(record.site_id, number)):
                    continue
                versions[number] = dict(number=number, deployed_at=None, source_revision=None,
                                        url=f"/s/{quote(record.site_id, safe='')}--r{number}/")
        return [versions[number] for number in sorted(versions)]

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
                self._saved_sites = raw
            except Exception as e:
                logger.warning(f"| ⚠️ Could not load deploy registry: {e}")
                self._sites = {}

    def _save(self) -> None:
        if not self._registry_path:
            return
        try:
            payload = {sid: rec.model_dump() for sid, rec in self._sites.items()}
            changed = {sid: value for sid, value in payload.items()
                       if value != self._saved_sites.get(sid)}
            atomic_json_update(
                self._registry_path,
                lambda current: {**current, **changed},
                default={},
            )
            self._saved_sites = payload
        except Exception as e:
            logger.warning(f"| ⚠️ Could not persist deploy registry: {e}")

    # --------------------------------------------------------------- spec resolution
    def refresh(self) -> None:
        """Read other deployers' changes without overwriting local in-flight edits."""
        if not self._registry_path:
            self._registry_path = str(path_manager.resolve_under(path_manager.get(P.DEPLOY), "sites.json"))
        try:
            stat = os.stat(self._registry_path)
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if stamp == self._registry_stamp:
                return
            with open(self._registry_path) as stream:
                raw = json.load(stream)
            for sid, value in raw.items():
                local = self._sites.get(sid)
                # A deploy coroutine still owns this object while awaiting readiness.
                # Reloading identical bytes would detach its subsequent RUNNING update.
                if local is not None and local.model_dump() == value:
                    continue
                if local is None or local.model_dump() == self._saved_sites.get(sid):
                    self._sites[sid] = SiteRecord(**value)
                    self._saved_sites[sid] = value
            self._registry_stamp = stamp
        except (OSError, ValueError):
            return

    @staticmethod
    def public_urls(record: SiteRecord) -> Dict[str, str]:
        """One authority for public links; `record.url` remains the backend URL."""
        base = os.environ.get("GATEWAY_PUBLIC_BASE", "").strip().rstrip("/")
        if not base:
            return {}
        name = quote(record.site_id, safe="")
        urls = {"site_url": f"{base}/s/{name}/"}
        if record.release_number:
            urls["release_url"] = f"{base}/s/{name}--r{record.release_number}/"
        return urls

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
        self.refresh()
        previous = self._sites.get(request.site_id)
        # Decide the backend *before* materializing inline content — materialization sets
        # source_dir, which would otherwise mask the "local source ⇒ host by default" rule.
        backend = self._backend_kind(request, previous)

        # Lightweight path: turn inline content/files into a source_dir the normal flow
        # can upload. git_url and an explicit source_dir take precedence and skip this.
        if (request.content or request.files) and not (request.source_dir or request.git_url):
            request.source_dir = self._materialize_inline(request)

        spec = self._resolve_spec(request)  # raises on bad profile / missing custom.start
        source_revision = self._source_revision(request)

        # Freeze source before stopping the live version or allocating resources.
        # A failed/conflicting archive must not take a working site down.
        pinned = self._split_release(request.site_id)
        archived_source = ""
        if pinned is not None:
            release_number = pinned[1]
        else:
            release_number = int(getattr(previous, "release_number", 0) or 0)
            recipe_changed = previous is not None and any(
                (previous.request or {}).get(key) != request.model_dump().get(key)
                for key in ("runtime", "env", "overrides", "backend")
            )
            if previous is None or previous.source_revision != source_revision or recipe_changed:
                release_number += 1
            if not request.git_url and request.source_dir and os.path.isdir(request.source_dir):
                archived_source = self._archive_release(request.site_id, release_number, request.source_dir)

        if previous is not None and previous.status not in {SiteStatus.STOPPED, SiteStatus.FAILED}:
            await self.stop_site(request.site_id, include_versions=False)

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
        spec.env = {**spec.env, "PORT": str(spec.port),
                    "BASE_PATH": f"/s/{quote(request.site_id, safe='')}/"}

        rec = SiteRecord(
            site_id=request.site_id,
            runtime=spec.runtime,
            status=SiteStatus.BUILDING,
            port=spec.port,
            image=spec.image,
            backend=backend,
            reuse_key=request.site_id,
            release_number=release_number,
            source_revision=source_revision,
            created_at=self._sites.get(
                request.site_id, SiteRecord(site_id=request.site_id, runtime=spec.runtime)
            ).created_at
            or _now(),
            updated_at=_now(),
            log_path=log_path,
            request=request.model_dump(),
            versions=list(previous.versions) if previous else [],
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
                    await self._upload_dir(sandbox, archived_source or request.source_dir, spec.workspace_root)

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
            rec.deployed_at = rec.updated_at = _now()
            self._record_version(rec)
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
        self.refresh()
        return list(self._sites.values())

    async def get_site(self, site_id: str) -> Optional[SiteRecord]:
        await self._ensure_initialized()
        self.refresh()
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

    @staticmethod
    def _split_release(name: str) -> Optional[tuple]:
        """``("echo-ark", 3)`` for ``"echo-ark--r3"``, else ``None``."""
        if "--r" not in name:
            return None
        base, _, suffix = name.rpartition("--r")
        if not base or not suffix.isdigit():
            return None
        return base, int(suffix)

    def resolve_url(self, name: str) -> Optional[str]:
        """The registered backend address, including container exposure/mapping."""
        if not self.resolve_port(name):
            return None
        record = self._sites.get(name)
        if record is None:
            split = self._split_release(name)
            record = self._sites.get(split[0]) if split else None
        return record.url if record else None

    async def ensure_release(self, name: str) -> Optional[int]:
        """The port serving one pinned release, starting it from its archive if needed.

        Concurrent first visits share one launch. Every visit refreshes the idle timer.

        An archived release is served by deploying it as an ordinary site under its own
        pinned name, so it reaches the visitor through the same relay as anything else
        rather than a second serving path that could drift from the first. Older releases
        stay stopped until somebody asks for one, and go back to being files afterwards.
        """
        split = self._split_release(name)
        if split is None:
            return None
        async with self._release_locks.setdefault(name, asyncio.Lock()):
            return await self._serve_release(name, *split)

    async def _serve_release(self, name: str, base: str, release: int) -> Optional[int]:

        await self._ensure_initialized()
        current = self._sites.get(base)
        if current is not None and int(getattr(current, "release_number", 0) or 0) == release:
            if port := self.resolve_port(base):
                return port

        running = self.resolve_port(name)
        if running:
            self._release_seen[name] = time.time()
            return running

        archive = self._release_dir(base, release)
        if not os.path.isdir(archive):
            return None

        # Reuse the release's own deploy request: its runtime and overrides are what made
        # those bytes serveable, and re-deriving them here would be a second opinion about
        # a site that already has one.
        metadata_path = self._version_metadata(base, release)
        if os.path.isfile(metadata_path):
            with open(metadata_path) as handle:
                stored = dict(json.load(handle)["request"])
        else:
            # Compatibility for archives created before recipe snapshots existed.
            stored = dict((getattr(current, "request", None) or {}))
        stored.update(
            site_id=name,
            source_dir=archive,
            content=None,
            files=None,
            git_url=None,
            backend=stored.get("backend") or "host",
            port=None,
        )
        try:
            await self.deploy(DeployRequest(**stored))
        except Exception as exc:
            logger.warning(f"| 📦 could not start release {release} of '{base}': {exc}")
            return None
        self._release_seen[name] = time.time()
        await self._reap_idle_releases()
        return self.resolve_port(name)

    async def _reap_idle_releases(self) -> None:
        """Stop pinned releases nobody has opened lately.

        Only sites this manager started on demand are candidates — a release someone
        deployed by that name themselves is theirs, not scratch space to reclaim.
        """
        now = time.time()
        # A pinned release this process did not start is one a previous process left
        # behind: the registry survives a restart but the last-seen times do not. Give it
        # a first sighting now rather than reaping it on the spot, so it still gets a full
        # idle window and a visitor mid-read is not cut off.
        for name, rec in list(self._sites.items()):
            if rec.status is SiteStatus.RUNNING and self._split_release(name):
                self._release_seen.setdefault(name, now)

        cutoff = now - _RELEASE_IDLE_S
        for name, seen in list(self._release_seen.items()):
            if seen > cutoff:
                continue
            self._release_seen.pop(name, None)
            record = self._sites.get(name)
            if record is None or record.status is not SiteStatus.RUNNING:
                continue
            try:
                await self.stop_site(name)
            except Exception as exc:
                logger.warning(f"| 📦 could not stop idle release '{name}': {exc}")

    def public_names(self) -> List[str]:
        """Every name that currently resolves, for diagnostics and the 404 body."""
        return sorted(
            name for name, rec in self._sites.items()
            if rec.status is SiteStatus.RUNNING and rec.port
        )

    def public_pages(self) -> List[Dict[str, Any]]:
        """Read-only page index: no deployment requests, source files, or secrets."""
        self.refresh()
        return [{"name": record.site_id, "title": record.request.get("title") or record.site_id,
                 "kind": record.request.get("kind") or "website", "status": record.status.value,
                 "url": f"/s/{quote(record.site_id, safe='')}/",
                 "created_at": record.created_at, "deployed_at": record.deployed_at,
                 "updated_at": record.updated_at, "version": record.release_number,
                 "versions": self.version_history(record)}
                for record in self._sites.values()]

    async def stop_site(self, site_id: str, *, include_versions: bool = True) -> SiteRecord:
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

        # Explicit shutdown includes archive servers; replacing the latest version does
        # not interrupt readers comparing previously published versions.
        if include_versions and self._split_release(site_id) is None:
            for pinned in self._pinned_releases_of(site_id):
                try:
                    await self.stop_site(pinned)
                except Exception as exc:
                    logger.warning(f"| 📦 could not stop pinned release '{pinned}': {exc}")
        return rec

    def _pinned_releases_of(self, site_id: str) -> List[str]:
        """Names of the running ``<site_id>--r<n>`` archives, if any."""
        return [
            name for name, rec in list(self._sites.items())
            if rec.status is SiteStatus.RUNNING
            and (self._split_release(name) or (None,))[0] == site_id
        ]

    async def redeploy(self, site_id: str) -> SiteRecord:
        """Tear down and rebuild a site from its stored request (new URL likely)."""
        await self._ensure_initialized()
        rec = self._sites.get(site_id)
        if rec is None or not rec.request:
            raise ValueError(f"No redeployable request stored for site {site_id!r}")
        request = DeployRequest(**rec.request)
        await self.stop_site(site_id, include_versions=False)
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
