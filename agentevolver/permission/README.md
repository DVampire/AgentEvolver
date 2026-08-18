---
name: permission
description: "Evaluates command and filesystem operations against explicit permission modes and policies. It also applies size and binary-file safeguards."
version: 1.0.0
type: module
category: permission
requirements: []
metadata: {}
---
# Permission

Evaluates command and filesystem operations against explicit permission modes and policies.
It also applies size and binary-file safeguards.

| File | Responsibility |
|---|---|
| `types.py` | Modes, operations, rules, validation, and helper checks |
| `context.py` | Registered entity policies |
| `server.py` | Public `permission_manager` enforcement facade |

Permission authorizes a proposed operation; Tool and Sandbox remain responsible for
executing it safely.

## Modes

| Mode | Refuses |
|---|---|
| `read_only` | anything classified as writing, destructive, admin or unknown; output redirects; composed commands |
| `workspace_write` | system-admin commands |
| `danger_full_access` | nothing, *except* the one rule below |

## The shared extension library is never written by bash

One rule applies in every mode, `danger_full_access` included: a command that names an
absolute path inside the shared `extension/` tree as somewhere it writes is refused.

Not access control. That tree is written by promotion, which in the same code path records
the version under `.versions/`, the rollback backup under `.promotion-backups/`, and the
registry entry in `manifest.json`, after validating the component. A redirect straight into
the tree updates none of them: the component is present while the registry says it is not,
the next promotion of that name overwrites it with no backup, and a rollback restores a
version that was never recorded. `danger_full_access` says this machine's system commands
are trusted — a claim about the host, not a licence to bypass the framework's own
bookkeeping.

Reads are untouched, as is every other path: `/tmp`, package installs, compiling in the
checkout. A run's own staging tree is not affected either, since it lives under
`output/<owner>/sessions/<id>/` rather than inside the shared library.

**It is a guard, not a boundary.** The written paths are found by reading redirect targets
and the arguments of commands whose job is to write; a path built inside a Python string, a
heredoc or a subprocess is invisible to it, and always will be. What it catches is a path
written out plainly — which is what an agent produces when it is following an instruction
about where to put something, and is how a generate run once wrote its workflow into the
shared tree and then failed promotion for it. The real boundary is the container.
