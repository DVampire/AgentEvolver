---
name: paths
description: "Single source of truth for generated output, shared components, and durable memory paths."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata:
  document_version: 1
---
# Paths

Every path the framework writes is declared in one table and resolved through
`path_manager`. Nothing else joins path fragments, so **this module is the disk
contract** — moving a directory is a one-line edit instead of a hunt through the
gateway, the sandbox, the IDE and half a dozen others.

```python
from agentevolver.paths import P, path_manager

path_manager.bind_session(owner, session_id)   # once, when the run's session opens
path_manager.get(P.SESSION_WORKSPACE)          # anywhere after that, no arguments
path_manager.get(P.PORTS)                      # machine-level, never needed a session
```

## Storage roots

| Root | Holds | Lifetime |
|---|---|---|
| `output/` | generated, machine- and user-specific state | disposable |
| `extension/` | shared, durable components (skills, tools, workflows, canvas) | versioned with the project |
| `memory/` | owner/project/actor-scoped Markdown notes | survives output cleanup; not versioned |

`writable_roots()` returns exactly these, so the rule is testable rather than a
convention people remember — see `tests/test_paths.py`.

| Variable | Moves |
|---|---|
| `AGENTEVOLVER_HOME` | the whole tree |
| `AGENTEVOLVER_EXTENSION_ROOT` | `extension/` alone (a shared component library on another volume) |
| `AGENTEVOLVER_MEMORY_ROOT` | `memory/` alone (a persistent, host-owned volume) |

All are resolved here and nowhere else. `extension/` used to have three
answers — `extension_root()` resolved it against `cwd`, skill and connector
joined `"extension/skill"` themselves, and the layout put it under
`AGENTEVOLVER_HOME` — so setting that variable relocated generated state and
left every shared component behind. Likewise `project_path()`, which configs use
for `project_root`/`workspace_root`/`log_root`, now resolves through
`project_dir()`: otherwise a config-started run wrote to `./output` while the
gateway wrote to `$AGENTEVOLVER_HOME/output`.

There used to be a third location, `./.agentevolver`, holding the port registry,
the sandbox ledger, deploy workspaces and extension staging. The container
created it as **root**, and `scripts/serve-ui.sh`'s chown loop only walks
`output/`, so the host user could neither edit nor delete it. Those all live
under `output/.runtime/` now.

`memory/` is a deliberate separate lifetime, not another runtime scratch directory.
Container ownership cleanup covers the default root; an external memory volume must
be provisioned with the host user's ownership. Declaring a storage root does not grant
agents unrestricted access to it. Browser-only agents receive no filesystem memory index.

## The tree

```
output/
  .runtime/                     machine-level — belongs to the host, not a user
    ports.json                  port registry
    sandbox_ledger.json         crash-safe container reaping
    deploy/                     deploy workspaces
    checkpoints/<run_id>.json   workflow run checkpoints
    staging/<project_key>/      extension staging
  <owner>/
    log/                        output produced before anything bound a session
    state/                      durable, survives every session
      files/  flows/  ide/{extensions,user-data,home}
    sessions/<session_id>/      disposable
      workspace/                the files agent, canvas and IDE all share
      log/                      this run's logs, trace and memory
      extension/                staging: what this run built, before promotion
      session.json              identity, so the session survives a restart
    runs/<run_id>/              direct (non-gateway) runs
extension/                      shared components
```

## Keys are an enum, not strings

`P` is a `str` Enum, so a typo is a static error with editor completion rather
than a path silently containing a literal `{owner}`. `get()` also validates
placeholders: asking for `SESSION_WORKSPACE` with only `owner` raises
`session_workspace needs ['session_id']` instead of creating a directory called
`{session_id}` that is painful to trace back later.

## One task, one directory — however it was started

A task started from a local config and the same task started from the browser
resolve to the *same* place. Both build their sandbox from `P.SESSION`:

| Entry point | Sandbox root |
|---|---|
| `examples/run_*`, `agent_manager` | `ensure_session_sandbox(ctx)` → `output/<owner>/sessions/<id>` |
| Gateway (`session.create`) | `path_manager.get(P.SESSION, ...)` → `output/<owner>/sessions/<id>` |

This used to diverge: the local path took `config.project_root / <id>` while the
gateway used its own join, so the two produced different trees for identical
work. `ensure_session_sandbox` resolves the layout and nothing else — its
`project_root` parameter is gone, having had no caller.

## The bound session

A run's roots are *resolved*, never *carried*. `bind_session(owner, session_id)`
is called once — by the direct entry points before any manager initializes, and
by the gateway per task on its serialized path — and after that every
session-scoped key answers for that run:

```python
path_manager.get(P.SESSION_EXTENSION)          # this run's staging tree
path_manager.get(P.SESSION_PLAN)               # sessions/<id>/plan/plan.md
path_manager.session_roots()["workspace"]      # and the rest of them, by name
```

They used to be computed once and then packed into `ctx.extra` as six strings,
handed down through every manager, agent, hook and tool. Two things went wrong.
The values drifted from their names — `ctx.extra["extension_root"]` was the
session's **writable staging tree** while `config.extension_root` was the
**shared library** — so one name meant two opposite directories depending on
which module read it, and an agent told the wrong one wrote where promotion
refused to look. And anything holding the dict could rewrite it, which left this
table advisory: a copy in flight was as authoritative as the table itself.
`session_roots()` names them apart (`extension` vs `shared_extension`) so the
distinction survives being read.

`unbind_session()` is not tidiness. The sandbox boundary is enforced *only* for a
bound run, so a leaked binding turns unrelated code into a run whose allowed
roots belong to somebody else — `tests/conftest.py` unbinds around every test for
exactly this reason.

## Overrides: the path the table cannot compute

A task running inside a container sees its workspace at the mount point, and no
host-side table can derive a mount point from a host path. That one case goes
through `override()`:

```python
path_manager.override(P.SESSION_WORKSPACE, "/workspace")   # what the agent sees
path_manager.get(P.SESSION_WORKSPACE)                      # → /workspace
path_manager.get(P.SESSION_WORKSPACE, owner=o, session_id=s)  # same override if (o, s) is bound
```

An override answers the unparameterised call and a call explicitly naming the bound
session. Naming a different session resolves its ordinary layout. A harness needing the
original host mount path must retain that path before overriding; explicitly repeating
the current session ID is not a way to bypass its override. Overrides are cleared when
a different session binds.

The runtime leases the bound session until its processes finish cleanup. During a lease,
rebinding to another session, unbinding, and changing shared overrides are refused. Set
container mappings before spawning. Separate root sessions still need separate OS
processes; a context variable alone cannot isolate the other singleton managers.

For host Git child dispatch, `workspace(path)` temporarily maps only workspace keys in
the current asyncio task and its children. Exiting the context restores the caller's
mapping. Plan, log, extension, and registry roots stay attached to the shared run, and no
rebind listeners are invoked. This keeps private worktrees separate without moving the
root session or implying an OS sandbox.

## Config tags are direct-run namespaces

When a config omits `project_root`, `process_general()` asks PathManager for
`P.OWNER` using this precedence:

1. explicit `output_owner`;
2. config `tag`;
3. `local` as the compatibility fallback.

Thus `tag = "swebench_pro_agent_baseline"` produces
`output/swebench_pro_agent_baseline/`, and its sessions, request pages and result
summaries stay in that namespace. Gateway calls still pass the real account owner
explicitly, so browser sessions remain under `output/<owner>/sessions/...`.
An explicit `project_root` remains an override for deployments that intentionally
place output elsewhere.

This distinction is deliberate: PathManager owns framework layout (sessions,
logs, traces, checkpoints, reports and runtime state). Paths *inside* a checked-out
task repository, an external dataset, or a system installation are input data and
remain ordinary `Path` operations; declaring those dynamic structures in the
global layout would make the disk contract less accurate, not more.
