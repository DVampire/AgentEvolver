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

This preserves source and deployment configuration, not a snapshot of external
databases or mutable application data. Direct `git_url` deployments do not yet
archive cloned source: use a checked-out local source directory when reproducible
version history is required. Legacy archives without recipe metadata retain their
source, but can only fall back to the site's last known recipe.
