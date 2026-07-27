# vscode — full VS Code in the browser, per session

Extends `gitpod/openvscode-server:latest` with a small terminal toolset and an
entrypoint that points VS Code at mounted, owner-scoped state. One container per
gateway session; the frontend embeds it in an iframe.

## What it adds
- **root user** — the stock image runs as uid 1000, but workspace files belong
  to the host user. Running as root keeps them writable; `scripts/serve-ui.sh`
  already chowns `output/` back to the host owner periodically.
- **nothing else** — no apt layer, so the build needs no network beyond the
  base pull. The stock image already carries `git`/`curl`/`bash`, and VS Code
  bundles its own ripgrep for search. Heavy project toolchains stay in the base
  container; this image is an editor, not the agent's execution environment, so
  the integrated terminal is a plain shell without the project's Python env.
- **entrypoint-vscode** — starts openvscode-server on `:3000` against the
  mounted directories below. OpenSandbox ignores the image ENTRYPOINT, so
  `VscodeSandbox` passes this script explicitly (same as chrome-vnc).

## Mounts
| Container path | Host source | Scope |
|---|---|---|
| `/workspace` | `session.sandbox.workspace_root` | **per session** — the same files the agent edits |
| `/ide/extensions` | `output/<owner>/state/ide/extensions` | **per owner** — installed plugins survive new sessions |
| `/ide/user-data` | `output/<owner>/state/ide/user-data` | **per owner** — settings and keybindings |

The container never mounts the Docker socket, so its integrated terminal cannot
reach the host daemon.

## Ports
- `3000` — openvscode-server: HTTP **and** the workbench WebSocket, same port.

Served at the **root path** of a per-session host (`<sid>.ide.localhost`) so VS
Code's absolute asset paths (`/stable-<commit>/static/...`) resolve untouched.
See [`agentevolver/ide/README.md`](../../agentevolver/ide/README.md) for the
full routing chain.

## Extensions
openvscode-server uses the **Open VSX** registry, not the Microsoft
Marketplace. Microsoft-licensed extensions (Pylance, the official C# and Remote
packs) are not published there and cannot be installed.

## Build
Built automatically on first use by `VscodeSandbox._ensure_image()`, or:

```bash
docker build -t agentevolver/vscode:latest docker/vscode/
```
