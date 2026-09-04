"""Deploy tool — run a service (static site / SPA / API) in a sandbox and get a URL.

Thin LLM-facing verb over ``deployment_manager``. Each site is an isolated
container bound to its own URL; multiple sites coexist (keyed by ``site_id``).
The per-framework knowledge lives in pluggable deploy *profiles* (``runtime``),
so this tool stays stable as new target types are added.
"""

import os
import socket
import time
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from agentevolver.deploy import DeployRequest, deployment_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

#: How long a release waits for an independent verdict before shipping without one.
#: Absent acceptance is a quality fact recorded against the release; it is not a reason
#: the pipeline may never move again. Long enough for a browser round to finish, short
#: enough that a subscriber whose process died does not strand the run.
ACCEPTANCE_TIMEOUT_S = 900.0

_DESCRIPTION = "Deploy and manage web apps — from a one-call inline HTML page to a full frontend/backend project — each bound to a URL."

_GUIDANCE = """
Deploy a web app and bind it to a reachable URL, then manage deployed sites. Each site is keyed by `site_id`; deploy many and each gets its own URL. Spans lightweight (a single inline HTML page served locally, instantly) to heavy (a full frontend build or backend service in an isolated container).

### Actions (pass `action`)
- `preview`: start the current source without publishing a release event. Under a website
  iteration contract, test this exact URL before `deploy`; the source hash must still match.
- `deploy`: publish a site and return its URLs. Args:
  - `site_id` (str, required): stable id / reuse key for the site.
  - `runtime` (str): `static` (plain HTML/CSS/JS or a pre-built SPA — the frontend/artifact default), `node` (build a React/Vue/Vite app and serve it), `python` (FastAPI/Flask/ASGI backend via uvicorn), `custom` (you supply image/build/start in `overrides`), `llm` (NOT implemented yet). Default `static`.
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
"""

_EXAMPLES = [
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
        urls = {"internal_url": internal}
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
        """The address that outlives this deployment, when a gateway can serve it.

        `site_url` follows the site across releases; `release_url` pins this one. Both go
        through the gateway rather than at a port, because the port is chosen fresh on
        every redeploy — an address built from one names a deployment, not a site, and
        every link handed out with it stops working at the next release.

        Absent a gateway base, nothing is added: a caller then sees only the port-based
        URLs it always saw, rather than an address that resolves nowhere.
        """
        base = (os.environ.get("GATEWAY_PUBLIC_BASE") or "").strip().rstrip("/")
        if not base or not rec.site_id:
            return {}
        named = {"site_url": f"{base}/s/{rec.site_id}/"}
        release = int(getattr(rec, "release_number", 0) or 0)
        if release:
            named["release_url"] = f"{base}/s/{rec.site_id}--r{release}/"
        return named

    @staticmethod
    def _previous_release_blocker(ctx: Any) -> str:
        """Keep the release loop closed: observe feedback before publishing again.

        Acceptance is keyed by (release, subscriber) and NOT by the subscriber's turn
        number. Those were treated as the same thing — `turn_success[release_number]` —
        on the assumption that a subscriber's Nth turn is always release N. A subscriber
        that failed its first turn and was asked to try again produced turn 2, so
        `turn_success[1]` stayed False for the rest of the run and no later release could
        ever ship. Measured: 58 of 133 builder steps, 43% of the run, spent retrying
        deploy and done against a gate that could not open.

        Turn numbers are the runtime's own immutable record of how many times a process
        ran. Which release a turn was *about* is a fact of this protocol, so this
        protocol records it.
        """
        extra = getattr(ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        history = extra.get("deployment_release_history")
        if not isinstance(contract, dict) or not isinstance(history, list) or not history:
            return ""

        release_number = len(history)
        acceptance = DeployTool._release_acceptance(contract, release_number)
        subscribers = [str(job_id) for job_id in contract.get("subscriber_job_ids") or []]

        pending, failed = [], []
        for job_id in subscribers:
            state = DeployTool._acceptance_state(contract, release_number, job_id)
            if state == "accepted":
                continue
            (failed if state == "failed" else pending).append(job_id)

        if pending:
            waited = DeployTool._release_wait_seconds(contract, release_number)
            if waited < ACCEPTANCE_TIMEOUT_S:
                remaining = int(ACCEPTANCE_TIMEOUT_S - waited)
                return (
                    f"release {release_number} subscriber turns are not complete: "
                    f"{', '.join(pending)} (waiting up to {remaining}s more)"
                )
            # Absent acceptance is a quality fact about the release, not a reason the
            # deployment pipeline may never move again. It is recorded and the gate
            # opens; the release history carries who never reported.
            for job_id in pending:
                acceptance[job_id] = {"status": "absent", "attempts": 0}
            logger.warning(
                f"| ⏳ release {release_number} proceeding without acceptance from "
                f"{', '.join(pending)} after {int(waited)}s"
            )

        if failed:
            return (
                f"release {release_number} was rejected by {', '.join(failed)}. "
                "Fix what they reported and ask the same subscriber to verify the fix "
                "with send_message_tool; a passing retry replaces this verdict."
            )

        collected = dict(contract.get("collected_turns") or {})
        unread = [
            job_id for job_id in subscribers
            if int(collected.get(job_id) or 0) < 1
        ]
        if unread:
            return (
                f"release {release_number} feedback must be read with job__output "
                f"before another deploy: {', '.join(unread)}"
            )
        return ""

    # -- acceptance, keyed by (release, subscriber) ---------------------------

    @staticmethod
    def _release_acceptance(contract: Dict[str, Any], release_number: int) -> Dict[str, Any]:
        """The per-subscriber acceptance record for one release, created on demand."""
        table = contract.setdefault("release_acceptance", {})
        return table.setdefault(str(release_number), {})

    @staticmethod
    def _acceptance_state(
        contract: Dict[str, Any], release_number: int, job_id: str
    ) -> str:
        """accepted / failed / absent / pending for one subscriber on one release."""
        recorded = DeployTool._release_acceptance(contract, release_number).get(job_id)
        if isinstance(recorded, dict):
            return str(recorded.get("status") or "pending")
        return "pending"

    @staticmethod
    def record_acceptance(
        ctx: Any, job_id: str, *, success: bool, turn: int
    ) -> str:
        """Record what a subscriber said about the CURRENT release.

        Called wherever a subscriber's turn is collected. A later attempt overwrites an
        earlier verdict for the same release, which is what makes a rejection something
        a run can recover from rather than a terminal state.
        """
        extra = getattr(ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        history = extra.get("deployment_release_history")
        if not isinstance(contract, dict) or not isinstance(history, list) or not history:
            return ""
        release_number = len(history)
        acceptance = DeployTool._release_acceptance(contract, release_number)
        previous = acceptance.get(str(job_id)) or {}
        acceptance[str(job_id)] = {
            "status": "accepted" if success else "failed",
            "attempts": int(previous.get("attempts") or 0) + 1,
            "turn": int(turn),
        }
        return acceptance[str(job_id)]["status"]

    @staticmethod
    def _release_wait_seconds(contract: Dict[str, Any], release_number: int) -> float:
        """How long this release has been waiting for its first acceptance."""
        started = contract.setdefault("release_wait_started", {})
        key = str(release_number)
        if key not in started:
            started[key] = time.time()
        return max(0.0, time.time() - float(started[key]))

    @staticmethod
    def _preview_site_id(site_id: str, ctx: Any) -> str:
        context_id = str(getattr(ctx, "id", "") or "runtime")[:8]
        return f"{site_id}--preview-{context_id}"

    @staticmethod
    def _preview_blocker(ctx: Any, site_id: str, revision: str) -> str:
        extra = getattr(ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        if not isinstance(contract, dict):
            return ""
        preview = contract.get("latest_preview")
        if not isinstance(preview, dict):
            return "preview the current workspace with deploy_tool action=preview first"
        if preview.get("site_id") != site_id:
            return f"latest preview belongs to site {preview.get('site_id')!r}, not {site_id!r}"
        if not revision or preview.get("source_revision") != revision:
            return "workspace source changed after preview; preview and verify the current revision again"
        return ""

    @staticmethod
    async def _publish_ready(rec, *, action: str, ctx: Any) -> Dict[str, Any]:
        """Broadcast a successful release to this task tree's live subscribers."""
        if ctx is None:
            return {}
        from agentevolver.runtime import kernel

        extra = getattr(ctx, "extra", None)
        contract = (extra or {}).get("website_runtime_contract")
        if not isinstance(contract, dict):
            return {}
        # ToolContext copies the ambient mapping but deliberately retains values by
        # reference. Mutate this Runtime-owned list in place so the parent AgentContext
        # observes the receipt used by its completion gate.
        history = (extra or {}).get("deployment_release_history")
        if not isinstance(history, list):
            history = []
            extra["deployment_release_history"] = history
        release_number = len(history) + 1
        # Stamped on the record so `/s/<site>--r<n>` can address this exact release
        # after the name has moved on to the next one.
        try:
            rec.release_number = release_number
        except Exception:  # noqa: BLE001 - a stamp must not fail a publish
            pass
        payload = {
            "release_number": release_number,
            "action": action,
            "site_id": rec.site_id,
            "runtime": rec.runtime,
            "url": rec.url,
            "source_revision": rec.source_revision,
            **DeployTool._access_urls(rec),
            "deployed_at": rec.updated_at,
        }
        try:
            sent, scoped, event = await kernel.publish_scoped(
                "deployment.ready",
                "deployment.ready",
                payload,
                ctx=ctx,
                sender=str(getattr(ctx, "name", "") or "deploy_tool"),
            )
            receipt = {
                **payload,
                "event_id": event.id,
                "topic": scoped.split("::", 1)[-1],
                "fanout": sent,
            }
        except Exception as error:  # noqa: BLE001
            logger.warning(f"| ⚠️ deployment.ready publication failed: {error}")
            receipt = {
                **payload,
                "event_id": "",
                "topic": "deployment.ready",
                "fanout": 0,
                "error": str(error),
            }
        history.append(receipt)
        return receipt

    async def __call__(
        self,
        action: Literal["preview", "deploy", "list", "get", "stop", "redeploy"] = "list",
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
            action: Operation to run: preview, deploy, list, get, stop, or redeploy.
            site_id: Stable site identifier. Required except for list.
            runtime: Deployment profile, such as static, node, python, or custom.
            source_dir: Absolute host directory containing the application.
            git_url: Repository URL to clone as the application source.
            content: Inline single-file application content.
            files: Inline mapping of relative paths to text content.
            filename: Destination filename used with content.
            backend: Execution backend: host, opensandbox, or auto.
            port: Optional application port override.
            env: Environment variables passed to the application.
            overrides: Deployment specification overrides such as start and health.
            **kwargs: Runtime-only values injected by the tool manager, including ctx.
        """
        action = (action or "list").lower().strip()
        try:
            if action in {"preview", "deploy"}:
                if not site_id:
                    raise KeyError("site_id")
                blocker = self._previous_release_blocker(kwargs.get("ctx"))
                if blocker:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"Deployment blocked: {blocker}",
                    )
                requested_site_id = site_id
                req = DeployRequest(
                    site_id=site_id,
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
                ctx = kwargs.get("ctx")
                if action == "preview":
                    req.site_id = self._preview_site_id(site_id, ctx)
                else:
                    preview_blocker = self._preview_blocker(ctx, site_id, revision)
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
                    contract = (getattr(ctx, "extra", None) or {}).get("website_runtime_contract")
                    preview = {
                        "site_id": requested_site_id,
                        "preview_site_id": rec.site_id,
                        "url": rec.url,
                        "source_revision": rec.source_revision,
                        **self._access_urls(rec),
                    }
                    if isinstance(contract, dict):
                        contract["latest_preview"] = preview
                    urls = self._access_urls(rec)
                    msg = f"✅ Preview for '{requested_site_id}' is running at {rec.url}"
                    if urls.get("public_url") and urls["public_url"] != rec.url:
                        msg += f"; remote browser URL: {urls['public_url']}"
                    return Response(
                        type=ResponseType.TOOL,
                        success=True,
                        message=msg,
                        data={**rec.model_dump(), **urls, "preview": True},
                    )
                if ok:
                    release = await self._publish_ready(
                        rec,
                        action="deploy",
                        ctx=kwargs.get("ctx"),
                    )
                msg = (
                    f"✅ '{rec.site_id}' deployed at {rec.url}"
                    if ok
                    else f"❌ '{rec.site_id}' status={rec.status.value}: {rec.error}"
                )
                urls = self._access_urls(rec)
                if ok and urls.get("public_url") and urls["public_url"] != rec.url:
                    msg += f"; remote browser URL: {urls['public_url']}"
                if release:
                    msg += (
                        f"; release {release['release_number']} queued to "
                        f"{release['fanout']} subscriber(s)"
                    )
                if ok:
                    contract = (getattr(ctx, "extra", None) or {}).get("website_runtime_contract")
                    preview = (
                        contract.pop("latest_preview", None) if isinstance(contract, dict) else None
                    )
                    preview_id = (preview or {}).get("preview_site_id")
                    if preview_id:
                        try:
                            await deployment_manager.stop_site(preview_id)
                        except Exception as error:  # noqa: BLE001
                            logger.warning(
                                f"| ⚠️ could not stop consumed preview {preview_id}: {error}"
                            )
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
                blocker = self._previous_release_blocker(kwargs.get("ctx"))
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
                preview_blocker = self._preview_blocker(
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
                    release = await self._publish_ready(
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
