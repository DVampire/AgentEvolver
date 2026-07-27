---
name: ide
description: "Full VS Code (openvscode-server) in the browser, one container per gateway session, editing the same workspace the agent edits. Human-facing: not an agent capability."
version: 1.0.0
type: module
category: interface
requirements: []
metadata:
  document_version: 1
---
# IDE

A real **VS Code in the browser** — extensions, integrated terminal, search,
git — editing the *same* workspace files the agent works on. One container per
gateway session, started on demand and reaped when idle.

Like the [canvas](../canvas/README.md), this is **human-facing**: the agent
never calls into it, and the IDE is not registered as a capability the meta
agent can see.

## Why it is served at a root path

VS Code emits **absolute** asset paths (`/stable-<commit>/static/...`). Served
under a sub-path they bypass the prefix and 404, and openvscode-server has no
base-path option. So the IDE gets the **root** of its own host instead:

```
localhost:5173                → AgentEvolver SPA
<session>.ide.localhost:5173  → that session's IDE, at "/"
```

Routing keys on the **Host** header, not the path, so every absolute asset URL
lands on the same rule for free. This is Gitpod's per-workspace-subdomain trick
narrowed to one port — `*.localhost` resolves to `127.0.0.1` without any DNS or
`/etc/hosts` entry, so remote access still forwards a **single** port.

## The chain

```
browser  <sid>.ide.localhost:5173/stable-…/static/x.js
   │  vite plugin matches the Host, prepends the proxy prefix
   ▼
gateway-resolved upstream  127.0.0.1:<ephemeral>/proxy/3000/stable-…/static/x.js
   │  the opensandbox proxy strips /proxy/3000
   ▼
openvscode-server :3000    /stable-…/static/x.js      ← always sees root
```

Both hops cancel out, so VS Code is never aware it is proxied and needs no
patching. The workbench WebSocket rides the same path and port.

## Port forwarding

The Host rule is general, not IDE-specific — prefix a port to reach **anything**
listening in that session's container:

```
<session>.ide.localhost:5173          → the IDE (port 3000)
<port>-<session>.ide.localhost:5173   → any other port in the same container
```

A dev server, a preview, a notebook, an OAuth callback listener — all reachable
with no per-tool support. Ports resolve on first use (exposing one is a round
trip to opensandbox) and are cached for the container's life.

**What this cannot do**, and why it is not a gap we can close: it exposes a
container port at a *URL*, not at `localhost:<port>` **on your machine**. Only a
native process on your machine can bind your loopback — that is exactly how VS
Code Remote-SSH does it, and a browser tab cannot bind ports at all. Codespaces
and Gitpod hand out URLs for the same reason.

So a tool whose OAuth `redirect_uri` is hard-coded to `localhost:<random port>`
still cannot complete its browser login in here: the browser resolves that on
your machine, where nothing is listening. That is a property of the OAuth
public-client flow in any remote environment, not of this setup — which is why
the remote-friendly alternatives exist and are the supported path:

| Tool | Callback-free sign-in |
|---|---|
| Claude Code | `CLAUDE_CODE_OAUTH_TOKEN` (mint once elsewhere with `claude setup-token`) — forwarded by `ide_manager` |
| Codex | `codex login --device-auth` (RFC 8628 device code) |

## Lifecycle

Lazy start on first open; a heartbeat plus every proxied request refresh the
idle clock; a reaper destroys containers idle past `idle_timeout_seconds`
(default 30 min). The gateway has no `session.close`, so **time** — not session
teardown — is what frees these. `max_instances` caps concurrent IDEs and evicts
the least recently used.

## State

| What | Scope | Why |
|---|---|---|
| `/workspace` | **per session** | the files the agent edits — same bytes, no copy |
| extensions | **per owner** | installed plugins survive new sessions |
| user data | **per owner** | settings and keybindings persist |
| `$HOME` | **per owner** | `~/.codex` and `~/.claude` live here, so an agent sign-in outlives the container |

Per-session containers with per-owner plugin state: a new session is isolated
but never makes you reinstall your extensions.

## Boundaries

- The container **never mounts the Docker socket**, so its terminal cannot reach
  the host daemon.
- That terminal is still a real shell inside the container, and it does not pass
  through the permission manager — its reach is the container plus the mounts
  above.
- Extensions come from **Open VSX**; Microsoft-licensed ones (Pylance, official
  C#/Remote packs) are not published there. **Claude Code**
  (`anthropic.claude-code`) and **Codex** (`openai.chatgpt`) are, and are
  installed on first use — each bundles its own CLI, so neither needs npm. See
  [`docker/vscode/README.md`](../../docker/vscode/README.md).

## Pieces

| Piece | Where |
|---|---|
| Image | [`docker/vscode/`](../../docker/vscode/) |
| Container handle | `agentevolver/sandbox/default/vscode.py` (`VscodeSandbox`) |
| Lifecycle | `server.py` (`ide_manager`) |
| Commands + resolve | `agentevolver/gateway/` (`ide.start` / `ide.status` / `ide.stop`) |
| Host routing | `frontend/vite.config.ts` |
| View | `frontend/src/ide/` |
