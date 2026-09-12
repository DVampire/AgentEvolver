---
name: deploy
description: "Deploys web apps — from a one-call inline HTML page served locally to a full frontend/backend project in an isolated container — and records their URLs, health, and lifecycle state."
version: 1.0.0
type: module
category: deploy
requirements: []
metadata: {}
---
# Deploy

`runtime="godot"` publishes a native Godot game as a browser playtest using the
`agentevolver/godot-play:4.7-v1` image (build `docker/godot-play` first). Pass the
project directory as `source_dir` and `backend="docker"`. The direct Docker backend
is distinct from `opensandbox`: it owns the image container, streams large assets,
publishes its port on loopback and routes HTTP/WebSocket traffic through the same
site gateway. The preview has independent saves and does not modify the running
Agent's workspace. Stop/redeploy removes the container and its preview saves.
See [game deployment setup](../../examples/tasks/game_development/README.md).

Deploys web apps and binds each to a reachable URL, keeping a persisted registry so sites
can be listed / stopped / redeployed. It spans a wide range in one interface:

- **Lightweight** — ship the page in the request itself (`content` for a single HTML file,
  `files` for a small `{path: text}` map). No host tree, no build. These default to the
  **host** backend, so a page is serving on a local port the moment you call `deploy` — the
  Claude-Code-artifacts experience, locally.
- **Heavy** — point at a real project (`source_dir` uploaded, or `git_url` cloned), build it,
  and run it in an **isolated container**. This is the default for a source tree.

Both frontend and backend are covered by the built-in profiles: `static` (plain HTML/CSS/JS
or a pre-built SPA), `node` (build a React/Vue/Vite app), `python` (a FastAPI/Flask/ASGI
backend via uvicorn), `custom` (caller supplies image/build/start), `llm` (placeholder).

| File | Responsibility |
|---|---|
| `types.py` | Deployment requests (inline content / source / git), specifications, records, statuses |
| `server.py` | Public `deployment_manager` and lifecycle operations |
| `default/` | Built-in deployment profiles (static / node / python / custom / llm) |

## Backend selection

`backend` on the request, then the `DEPLOY_BACKEND` env, then `auto` decide where a site
runs: `host` (local, no container — lightweight/instant) or `opensandbox` (isolated Docker
container — heavy). Inline `content`/`files` default to `host`; a `source_dir`/`git_url`
defaults to `auto` (container when Docker is reachable, else host). Host-backend ports are
allocated and de-conflicted through the central **port** registry (`deploy:<site_id>`), so
distinct sites get distinct ports and the whole map is visible in one place.

Deployment coordinates a target backend; process isolation and command execution belong to
the Sandbox module.

## Versioned previews and releases

Local source and inline deployments retain immutable source snapshots under
`sites/<site>/releases/r<N>`. The original deployment recipe is stored beside the
snapshot, outside the served directory. Registry version entries expose only the
version, revision, timestamp and URL, never environment credentials.

`/s/<site>/` follows the current deployment; `/s/<site>--r<N>/` opens a pinned
version, starting its archived source on demand even after the preview was stopped.
Previews have their own site identity and version sequence. Feedback round numbers
are separate from these persistent artifact versions. Both monitoring pages list
version history; old links and source are retained until explicitly removed.

An inactive archive-server record does not hide a running copy of the same release.
When a pinned release loses its backend, the gateway restores that release's own
archived source, without restarting the latest release. The relay bounds the wait for
upstream HTTP headers to 30 seconds and returns a visible 504 on timeout; this does not
limit the lifetime of an established event stream or streamed download.

Launcher teardown reclaims its sandbox resources and stops temporary previews. Published
sites become `DETACHED`: the persistent gateway restores them on the next visit from their
archived source, rather than from an edited working tree. An explicit stop remains `STOPPED`
and does not revive through the stable site URL. The first visit after teardown may take time
to start the service. A failed start returns 503 with a pointer to deployment diagnostics.

Host recipes retain the executable search PATH used at deployment, with the launcher's
Python bin directory available by default. An explicitly supplied PATH wins. This preserves
runtime lookup across a gateway restart without copying unrelated process environment
variables into the release recipe. It does not bundle installed interpreters or dependencies;
use a container profile when those must be portable to another host.

This preserves source and deployment configuration, not a snapshot of external
databases or mutable application data. Direct `git_url` deployments do not yet
archive cloned source: use a checked-out local source directory when reproducible
version history is required. Legacy archives without recipe metadata retain their
source, but can only fall back to the site's last known recipe.

## Task release contracts

Agent preparation only preserves public task declarations and shared `task_state`.
On `status`, `preview`, `deploy` or `redeploy`, the deploy tool asks deployment manager
to bind the declared policy and existing subscriber IDs. Deployment records live under
`ctx.extra.task_state` (`deployment_contract` and `deployment_release_history`), shared
across tool-context conversions and distinct from persistent site artifact versions.
Standalone manager callers may bind a context directly with `configure_task`.

`record_preview`, `preview_blocker`, `publish_release` and `consume_preview` manage the
preview/publication lifecycle. `collect_feedback` acknowledges a completed report only
when its owner reads the full output. `feedback_context` reports exact unread/current turns.

`deploy_tool(action="status")` exposes `release_status`: configured, ready, reason, release
counts, subscriber bindings and feedback. Query success is separate from readiness; a task
without a policy has `configured=false, ready=null`. The manager checks release count,
source revisions, fanout, acceptance and collection, but this status does not control
Agent termination. Before handoff the Agent checks it and either addresses unmet conditions
or reports them as blocked. Neither the base loop nor `done_tool` calls deployment manager.

Removing the hidden completion veto does not by itself recover an overflowing reviewer
or fix the separate failed-acceptance precondition that can block a repair preview.
