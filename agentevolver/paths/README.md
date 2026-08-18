---
name: paths
description: "Single source of truth for the on-disk layout: one table declaring every path the framework writes, resolved through path_manager. Two roots only — output/ for generated state, extension/ for shared components."
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

## Two roots, and only two

| Root | Holds | Lifetime |
|---|---|---|
| `output/` | generated, machine- and user-specific state | disposable |
| `extension/` | shared, durable components (skills, tools, workflows, canvas) | versioned with the project |

`writable_roots()` returns exactly these, so the rule is testable rather than a
convention people remember — see `tests/test_paths.py`.

| Variable | Moves |
|---|---|
| `AGENTEVOLVER_HOME` | the whole tree — both roots |
| `AGENTEVOLVER_EXTENSION_ROOT` | `extension/` alone (a shared component library on another volume) |

Both are resolved here and nowhere else. `extension/` used to have three
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
path_manager.get(P.SESSION_WORKSPACE, owner=o, session_id=s)  # → the host directory
```

An override answers the *unparameterised* call only, because ProgramBench needs
both answers in one process: the agent's view of its workspace, and the real
directory the harness is about to mount there. Overrides are cleared whenever a
new session binds, since they describe one run's environment.

## `tag` is a label, not a directory

Configs used to set `project_root = output/<tag>`, which put `output/meta_agent/`
beside `output/local/` — a config tag and an owner sharing one level, so a user
named `meta_agent` would collide. Nothing ever read `config.tag`; it was only a
local variable used to build that path. Configs now default to `output/local`,
and `bind_session_roots()` repoints them at the session sandbox the moment real
work starts, so per-run isolation comes from the session (or `runs/<run_id>`)
rather than from the tag.

That default was `output/.runtime/unbound` for a while, which put a run's own
pre-session logs in the machine-level tree — state that by definition belongs to
the host and outlives every run. A run's startup window is neither. It also came
with a `P.UNBOUND` layout entry that nothing ever resolved: the path existed only
as a string literal repeated across two dozen configs, so the table and the
configs agreed by coincidence rather than by construction. Both are gone.
