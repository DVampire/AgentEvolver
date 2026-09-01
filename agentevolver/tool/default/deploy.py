"""Deploy tool — run a web service (static site / SPA / API) in a sandbox and get a URL.

Thin LLM-facing verb over ``deployment_manager``. Each site is an isolated
container bound to its own URL; multiple sites coexist (keyed by ``site_id``).
The per-framework knowledge lives in pluggable deploy *profiles* (``runtime``),
so this tool stays stable as new target types are added.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from agentevolver.deploy import DeployRequest, deployment_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Deploy and manage web apps — from a one-call inline HTML page to a full frontend/backend project — each bound to a URL."

_GUIDANCE = """
Deploy a web app and bind it to a reachable URL, then manage deployed sites. Each site is keyed by `site_id`; deploy many and each gets its own URL. Spans lightweight (a single inline HTML page served locally, instantly) to heavy (a full frontend build or backend service in an isolated container).

### Actions (pass `action`)
- `deploy`: start a site, return its URL. Args:
  - `site_id` (str, required): stable id / reuse key for the site.
  - `runtime` (str): `static` (plain HTML/CSS/JS or a pre-built SPA — the frontend/artifact default), `node` (build a React/Vue/Vite app and serve it), `python` (FastAPI/Flask/ASGI backend via uvicorn), `custom` (you supply image/build/start in `overrides`), `llm` (NOT implemented yet). Default `static`.
  - App source — give exactly one:
    - `content` (str): inline single-file content (e.g. an HTML page) — the lightweight path, no files on disk needed. Served as `filename` (default `index.html`).
    - `files` (dict): inline `{relative_path: text}` map for a small multi-file app (e.g. `{"index.html": "...", "app.js": "..."}`, or a tiny backend `{"app.py": "...", "requirements.txt": "..."}`).
    - `source_dir` (str): host directory uploaded as the app.
    - `git_url` (str): repo cloned inside the container (needs network).
  - `filename` (str, optional): filename for `content` (default `index.html`).
  - `backend` (str, optional): `host` (local, no container — lightweight/instant), `opensandbox` (isolated Docker container — heavy/isolated), or `auto`. Inline `content`/`files` default to `host`; a `source_dir`/`git_url` defaults to `auto`.
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
- Backend: inline content/files run on the host by default (instant, no isolation); source_dir/git_url use the isolated container when Docker is available, else the host. Force per-deploy with `backend`, or globally with the `DEPLOY_BACKEND` env var. On the host backend, distinct sites get distinct ports automatically.
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
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    mutates: bool = True
    permission_mode: str = Field(default="danger_full_access", description="Runs build/start commands inside an isolated sandbox.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    @staticmethod
    def _site_line(rec) -> str:
        """Format one deployment record as a tab-separated line for the `list` view.

        Columns: site id, runtime, status, and URL (or "-" when not yet assigned).
        """
        return f"{rec.site_id}\t{rec.runtime}\t{rec.status.value}\t{rec.url or '-'}"

    async def __call__(
        self,
        action: Literal["deploy", "list", "get", "stop", "redeploy"] = "list",
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
            action: Operation to run: deploy, list, get, stop, or redeploy.
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
            if action == "deploy":
                if not site_id:
                    raise KeyError("site_id")
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
                rec = await deployment_manager.deploy(req)
                ok = rec.status.value == "running"
                msg = (f"✅ '{rec.site_id}' deployed at {rec.url}" if ok
                       else f"❌ '{rec.site_id}' status={rec.status.value}: {rec.error}")
                return Response(type=ResponseType.TOOL, success=ok, message=msg, data=rec.model_dump())

            if action == "list":
                sites = await deployment_manager.list_sites()
                if not sites:
                    return Response(type=ResponseType.TOOL, success=True, message="No deployed sites.")
                body = "\n".join(["site_id\truntime\tstatus\turl"] + [self._site_line(s) for s in sites])
                return Response(type=ResponseType.TOOL, success=True, message=body,
                                data={"sites": [s.model_dump() for s in sites]})

            if action == "get":
                if not site_id:
                    raise KeyError("site_id")
                rec = await deployment_manager.get_site(site_id)
                if rec is None:
                    return Response(type=ResponseType.TOOL, success=False, message=f"No such site {site_id!r}.")
                return Response(type=ResponseType.TOOL, success=True, message=self._site_line(rec), data=rec.model_dump())

            if action == "stop":
                if not site_id:
                    raise KeyError("site_id")
                rec = await deployment_manager.stop_site(site_id)
                return Response(type=ResponseType.TOOL, success=True, message=f"Stopped '{rec.site_id}'.", data=rec.model_dump())

            if action == "redeploy":
                if not site_id:
                    raise KeyError("site_id")
                rec = await deployment_manager.redeploy(site_id)
                ok = rec.status.value == "running"
                msg = (f"✅ '{rec.site_id}' redeployed at {rec.url}" if ok
                       else f"❌ redeploy '{rec.site_id}' status={rec.status.value}: {rec.error}")
                return Response(type=ResponseType.TOOL, success=ok, message=msg, data=rec.model_dump())

            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Unknown action {action!r}. Use deploy | list | get | stop | redeploy.")
        except KeyError as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Missing required arg: {e}")
        except Exception as e:
            logger.error(f"| ❌ deploy_tool {action} failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Error: {e}")
