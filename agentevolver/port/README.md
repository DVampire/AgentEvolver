---
name: port
description: "Central port registry — names the framework's well-known default ports and hands out / records host ports, persisted to the .agentevolver home so bindings are de-conflicted and discoverable."
version: 1.0.0
type: module
category: port
requirements: []
metadata: {}
---
# Port

Central port registry. Replaces ad-hoc port literals and one-off free-port
picking with:

- **Named defaults** (`server.py`) — the single source of truth for well-known
  ports: `GATEWAY` (9876), `OPENSANDBOX` (8080), `TRACE_UI` (8600), and the
  container-internal `CHROME_CDP` (9222) / `VNC` (5900) / `NOVNC` (6080).
- **`port_manager`** — `reserve(name, preferred)` returns a host port and records
  it; `release(name)`, `get(name)`, and `registry()` round out the API. Dynamic
  allocations (deploy sites, the Gateway/Trace bind) all go through it.

Allocations persist to `<home>/ports.json` (the `.agentevolver` home), so every
process and every run sees the same map of what is bound where.

Container-internal ports (`CHROME_CDP`/`VNC`/`NOVNC`) are fixed inside a sandbox
and mapped to ephemeral host ports by the opensandbox proxy — they never collide
on the host and so are constants, not registry entries.
