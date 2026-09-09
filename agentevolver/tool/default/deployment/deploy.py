"""Deploy tool — run a service (static site / SPA / API) in a sandbox and get a URL.

Thin LLM-facing verb over ``deployment_manager``. Each site is an isolated
container bound to its own URL; multiple sites coexist (keyed by ``site_id``).
The per-framework knowledge lives in pluggable deploy *profiles* (``runtime``),
so this tool stays stable as new target types are added.
"""

import os
import socket
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from agentevolver.deploy import DeployRequest, deployment_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Deploy and manage web apps and native Godot playtests, each bound to a gateway URL."

_GUIDANCE = """
Deploy a web app and bind it to a reachable URL, then manage deployed sites. Each site is keyed by `site_id`; deploy many and each gets its own URL. Spans lightweight (a single inline HTML page served locally, instantly) to heavy (a full frontend build or backend service in an isolated container).

When available, share `site_url` / `release_url` through the single gateway port, not the internal `url`. The app still serves routes at its internal root. Deploy supplies `BASE_PATH=/s/<site_id>/` during build/start and the gateway supplies `X-Forwarded-Prefix` per request (also for pinned releases). Generate browser resource, API and WebSocket URLs under that prefix; root-absolute `/api/...` or `/assets/...` URLs bypass the site's route. A relative URL must also account for nested client routes. Verify navigation and API calls at the public site_url, not only the internal port. The gateway does not rewrite arbitrary application JavaScript.

### Actions (pass `action`)
- `status`: inspect this task's release requirements, completed releases, exact feedback
  turns and acceptance readiness. No site_id is needed. Check this before handing off a
  requested deployment; `ready=false` reports unfinished release work, not a failed query.
  This tool checks deployment policy. It never ends or vetoes the Agent's task.
- `preview`: start the current source without publishing a release event. Under a website
  iteration contract, test this exact URL before `deploy`; the source hash must still match.
- `deploy`: publish a site and return its URLs. Args:
  - `site_id` (str, required): stable id / reuse key for the site.
  - `runtime` (str): `static` (plain HTML/CSS/JS or a pre-built SPA), `node` (React/Vue/Vite), `python` (ASGI backend), `godot` (native browser playtest), `custom` (image/build/start overrides), `llm` (not implemented). Default `static`.
  - App source — give exactly one:
    - `content` (str): inline single-file content (e.g. an HTML page) — the lightweight path, no files on disk needed. Served as `filename` (default `index.html`).
    - `files` (dict): inline `{relative_path: text}` map for a small multi-file app (e.g. `{"index.html": "...", "app.js": "..."}`, or a tiny backend `{"app.py": "...", "requirements.txt": "..."}`).
    - `source_dir` (str): host directory uploaded as the app.
    - `git_url` (str): repo cloned inside the container (needs network).
  - `filename` (str, optional): filename for `content` (default `index.html`).
  - `backend` (str, optional): `host` (local, no container — lightweight/instant), `opensandbox` (isolated Docker container — heavy/isolated), or `auto`. A local source (`content`/`files`/`source_dir`) defaults to `host`; only `git_url` defaults to `auto`. Redeploying an existing `site_id` keeps the backend it already runs on unless you pass this.
  - `port` (int, optional): override the profile's default port. On the host backend the port is allocated/de-conflicted through the central port registry.
  - `env` (dict, optional): environment variables.
  - `overrides` (dict, optional): field-level spec overrides — `image`, `build` (list of shell cmds), `start` (server cmd, MUST bind 0.0.0.0:$PORT), `workspace_root`, `health` ({type: http|command|none, path, command, timeout_s}). `custom` runtime REQUIRES `overrides.start`.
- `list`: list all sites with status + URL. No args.
- `get`: one site's full record. Args: `site_id`.
- `stop`: stop a site. Args: `site_id`.
- `redeploy`: tear down and rebuild a site from its stored request (URL may change). Args: `site_id`.

- Fastest path — publish a page: `deploy` with just `site_id` + `content` (the HTML). It serves on the host at `http://localhost:<port>` right away.
- The service MUST listen on `0.0.0.0` (not `127.0.0.1`) or the URL won't be reachable.
- `static` serves the files as-is; `node` needs a buildable project (has package.json); `python` defaults to the `app:app` entrypoint — override `start` for another (e.g. `uvicorn main:app --host 0.0.0.0 --port 8000`).
- Backend: anything local (inline content/files, or a source_dir) runs on the host by default — instant, and a plain `http://localhost:<port>` URL. Only git_url uses the isolated container when Docker is available. A site keeps its backend across redeploys; pass `backend` to move it, or set the `DEPLOY_BACKEND` env var globally. On the host backend, distinct sites get distinct ports automatically.

### Native Godot playtests
Use runtime=`godot`, backend=`docker`, and source_dir pointing at the directory
containing project.godot. The installed image `agentevolver/godot-play:4.7-v1`
provides native rendering and noVNC browser keyboard/mouse input. Deploy snapshots
the source into its own container; it never drives the Agent's development instance.
Share site_url/release_url under the gateway and record the version in plan.md.
The preview has separate saves, reset on redeployment, shared by visitors to that
instance. It does not stream audio. A reachable preview is not completed gameplay
acceptance or a native export. Republish stable revisions as development progresses.
`stop` and `redeploy` manage the game, display and streaming server together.
The direct Docker backend also works for other image-based deployment profiles;
its published port is loopback-only and reached through the gateway.
"""

_EXAMPLES = [
    '{"name":"deploy_tool","args":{"action":"deploy","site_id":"game-playtest","runtime":"godot","backend":"docker","source_dir":"/abs/workspace/game"}}',
    '{"name": "deploy_tool", "args": {"action": "deploy", "site_id": "hello", "content": "<h1>Hello</h1>"}}',
    '{"name": "deploy_tool", "args": {"action": "deploy", "site_id": "coffee-shop", "runtime": "static", "source_dir": "/abs/path/to/site"}}',
    '{"name": "deploy_tool", "args": {"action": "deploy", "site_id": "api", "runtime": "python", "files": {"app.py": "from fastapi import FastAPI\\napp=FastAPI()\\n@app.get(\'/\')\\ndef r(): return {\'ok\': True}", "requirements.txt": "fastapi"}}}',
]

@TOOL.register_module(force=True)
class DeployTool(Tool):
    """Deploy/manage sandboxed web services, each bound to a URL."""

    name: str = "deploy_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(
        default=False, description="Whether the tool may be evolved (self-optimized)"
    )
    mutates: bool = True
    permission_mode: str = Field(
        default="danger_full_access",
        description="Runs build/start commands inside an isolated sandbox.",
    )

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    def will_mutate(self, arguments: Dict[str, Any]) -> bool:
        return arguments.get("action", "list") not in {"status", "get", "list"}

    @staticmethod
    def _site_line(rec) -> str:
        """Format one deployment record as a tab-separated line for the `list` view.

        Columns: site id, runtime, status, and URL (or "-" when not yet assigned).

        A `<site>--r<n>` row is an older release someone opened, brought up from its
        archive by the visit itself and gone again once nobody is reading it. The caller
        never deployed it and cannot keep it, so the row says what it is: hiding it would
        be worse — it is a real process holding a real port — but leaving it to read as
        one more site the caller published invites managing something that manages itself.
        """
        line = f"{rec.site_id}\t{rec.runtime}\t{rec.status.value}\t{rec.url or '-'}"
        base, _, suffix = str(rec.site_id).rpartition("--r")
        if base and suffix.isdigit():
            line += f"\t(archived release {suffix} of {base}, served on demand)"
        return line

    @staticmethod
    def _access_urls(rec) -> Dict[str, str]:
        """Return loopback for agents plus a routable host URL for remote users."""
        internal = str(rec.url or "")
        urls = {"internal_url": internal, **DeployTool._named_urls(rec)}
        if not internal:
            return urls
        parsed = urlsplit(internal)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            urls["public_url"] = internal
            return urls
        host = (os.environ.get("DEPLOY_PUBLIC_HOST") or "").strip()
        if not host:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("8.8.8.8", 80))
                host = str(probe.getsockname()[0])
            except OSError:
                host = ""
            finally:
                probe.close()
        if host and parsed.port:
            urls["public_url"] = urlunsplit(
                (
                    parsed.scheme,
                    f"{host}:{parsed.port}",
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
        urls.update(DeployTool._named_urls(rec))
        return urls

    @staticmethod
    def _named_urls(rec) -> Dict[str, str]:
        return deployment_manager.public_urls(rec)

    # -- acceptance, keyed by (release, subscriber) ---------------------------

    @staticmethod
    def _preview_site_id(site_id: str, ctx: Any) -> str:
        context_id = str(getattr(ctx, "id", "") or "runtime")[:8]
        return f"{site_id}--preview-{context_id}"

    async def __call__(
        self,
        action: Literal["status", "preview", "deploy", "list", "get", "stop", "redeploy"] = "list",
        site_id: Optional[str] = None,
        runtime: str = "static",
        source_dir: Optional[str] = None,
        git_url: Optional[str] = None,
        content: Optional[str] = None,
        files: Optional[Dict[str, str]] = None,
        filename: str = "index.html",
        backend: Optional[str] = None,
        port: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        overrides: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Response:
        """Deploy, inspect, and tear down sites.

        Args:
            action: Operation to run: status, preview, deploy, list, get, stop, or redeploy.
            site_id: Stable site identifier. Required except for list and status.
            runtime: Deployment profile: static, node, python, godot (native browser playtest), or custom.
            source_dir: Absolute host directory containing the application.
            git_url: Repository URL to clone as the application source.
            content: Inline single-file application content.
            files: Inline mapping of relative paths to text content.
            filename: Destination filename used with content.
            backend: Execution backend: host, docker, opensandbox, or auto. Use docker for godot.
            port: Optional application port override.
            env: Environment variables passed to the application.
            overrides: Deployment specification overrides such as start and health.
            **kwargs: Runtime-only values injected by the tool manager, including ctx.
        """
        action = (action or "list").lower().strip()
        try:
            if action in {"status", "preview", "deploy", "redeploy"}:
                deployment_manager.prepare_task(kwargs.get("ctx"))
            if action == "status":
                status = deployment_manager.release_status(kwargs.get("ctx"))
                return Response(
                    type=ResponseType.TOOL, success=True, data=status,
                    message=(status["reason"] or "All declared release checks passed."),
                )
            if action in {"preview", "deploy"}:
                if not site_id:
                    raise KeyError("site_id")
                blocker = deployment_manager.feedback_blocker(kwargs.get("ctx"))
                if blocker:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"Deployment blocked: {blocker}",
                    )
                requested_site_id = site_id
                ctx = kwargs.get("ctx")
                extra = getattr(ctx, "extra", None) or {}
                req = DeployRequest(
                    site_id=site_id,
                    owner_session_id=extra.get("root_session_id") or getattr(ctx, "id", None),
                    stage="preview" if action == "preview" else "published",
                    runtime=runtime,
                    source_dir=source_dir,
                    git_url=git_url,
                    content=content,
                    files=files,
                    filename=filename,
                    backend=backend,
                    port=port,
                    env=env or {},
                    overrides=overrides or {},
                )
                revision = deployment_manager.source_revision(req)
                if action == "preview":
                    req.site_id = self._preview_site_id(site_id, ctx)
                else:
                    preview_blocker = deployment_manager.preview_blocker(ctx, site_id, revision)
                    if preview_blocker:
                        return Response(
                            type=ResponseType.TOOL,
                            success=False,
                            message=f"Deployment blocked: {preview_blocker}",
                        )
                rec = await deployment_manager.deploy(req)
                ok = rec.status.value == "running"
                release = {}
                if ok and action == "preview":
                    preview = {
                        "site_id": requested_site_id,
                        "preview_site_id": rec.site_id,
                        "version_number": rec.release_number,
                        "url": rec.url,
                        "source_revision": rec.source_revision,
                        **self._access_urls(rec),
                    }
                    deployment_manager.record_preview(ctx, preview)
                    urls = self._access_urls(rec)
                    msg = f"✅ Preview r{rec.release_number} for '{requested_site_id}' is running at {urls.get('release_url') or rec.url}"
                    if urls.get("public_url") and urls["public_url"] != rec.url:
                        msg += f"; remote browser URL: {urls['public_url']}"
                    return Response(
                        type=ResponseType.TOOL,
                        success=True,
                        message=msg,
                        data={**rec.model_dump(), **urls, "preview": True},
                    )
                if ok:
                    release = await deployment_manager.publish_release(
                        rec,
                        action="deploy",
                        urls=self._access_urls(rec),
                        ctx=kwargs.get("ctx"),
                    )
                msg = (
                    f"✅ '{rec.site_id}' deployed at {rec.url}"
                    if ok
                    else f"❌ '{rec.site_id}' status={rec.status.value}: {rec.error}"
                )
                urls = self._access_urls(rec)
                if ok:
                    msg += f"; version r{rec.release_number}: {urls.get('release_url') or rec.url}"
                if ok and urls.get("public_url") and urls["public_url"] != rec.url:
                    msg += f"; remote browser URL: {urls['public_url']}"
                if release:
                    msg += (
                        f"; release {release['release_number']} queued to "
                        f"{release['fanout']} subscriber(s)"
                    )
                if ok:
                    await deployment_manager.consume_preview(ctx)
                return Response(
                    type=ResponseType.TOOL,
                    success=ok,
                    message=msg,
                    data={**rec.model_dump(), **urls, "subscription_event": release or None},
                )

            if action == "list":
                sites = await deployment_manager.list_sites()
                if not sites:
                    return Response(
                        type=ResponseType.TOOL, success=True, message="No deployed sites."
                    )
                body = "\n".join(
                    ["site_id\truntime\tstatus\turl"] + [self._site_line(s) for s in sites]
                )
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=body,
                    data={"sites": [s.model_dump() for s in sites]},
                )

            if action == "get":
                if not site_id:
                    raise KeyError("site_id")
                rec = await deployment_manager.get_site(site_id)
                if rec is None:
                    return Response(
                        type=ResponseType.TOOL, success=False, message=f"No such site {site_id!r}."
                    )
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=self._site_line(rec),
                    data=rec.model_dump(),
                )

            if action == "stop":
                if not site_id:
                    raise KeyError("site_id")
                rec = await deployment_manager.stop_site(site_id)
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=f"Stopped '{rec.site_id}'.",
                    data=rec.model_dump(),
                )

            if action == "redeploy":
                if not site_id:
                    raise KeyError("site_id")
                blocker = deployment_manager.feedback_blocker(kwargs.get("ctx"))
                if blocker:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"Deployment blocked: {blocker}",
                    )
                current = await deployment_manager.get_site(site_id)
                if current is None or not current.request:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"No redeployable request stored for site {site_id!r}.",
                    )
                revision = deployment_manager.source_revision(DeployRequest(**current.request))
                preview_blocker = deployment_manager.preview_blocker(
                    kwargs.get("ctx"),
                    site_id,
                    revision,
                )
                if preview_blocker:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"Deployment blocked: {preview_blocker}",
                    )
                rec = await deployment_manager.redeploy(site_id)
                ok = rec.status.value == "running"
                release = {}
                if ok:
                    release = await deployment_manager.publish_release(
                        rec,
                        action="redeploy",
                        ctx=kwargs.get("ctx"),
                    )
                msg = (
                    f"✅ '{rec.site_id}' redeployed at {rec.url}"
                    if ok
                    else f"❌ redeploy '{rec.site_id}' status={rec.status.value}: {rec.error}"
                )
                if release:
                    msg += (
                        f"; release {release['release_number']} queued to "
                        f"{release['fanout']} subscriber(s)"
                    )
                return Response(
                    type=ResponseType.TOOL,
                    success=ok,
                    message=msg,
                    data={**rec.model_dump(), "subscription_event": release or None},
                )

            return Response(
                type=ResponseType.TOOL, success=False, message=f"Unknown action {action!r}."
            )
        except KeyError as e:
            return Response(
                type=ResponseType.TOOL, success=False, message=f"Missing required arg: {e}"
            )
        except Exception as e:
            logger.error(f"| ❌ deploy_tool {action} failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Error: {e}")
