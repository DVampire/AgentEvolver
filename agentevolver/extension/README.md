---
name: extension
description: "Manages generated extension manifests and their promotion into the active framework. Promotion is journaled and guarded by replay-based smoke checks."
version: 1.0.0
type: module
category: extension
requirements: []
metadata: {}
---
# Extension

Manages generated extension manifests and their promotion into the active framework.
Promotion is journaled and guarded by replay-based smoke checks.

| File | Responsibility |
|---|---|
| `types.py` | Manifest and component contracts |
| `server.py` | Extension registration and promotion facade |
| `journal.py` | Recoverable change journal |
| `smoke_gate.py` | Pre-promotion validation gate |

Extension coordinates installation; the owning Tool, Skill, Agent, or Workflow Manager
remains the source of truth after registration.

The tree carries nine module kinds: `tool`, `agent`, `prompt`, `skill`, `environment`,
`connector`, `workflow`, `memory` and `plugin`. A `plugin/<name>/` component has the same
shape as a built-in one — `plugin.py` beside `PLUGIN.md`, with `tools/` and `resources/` —
so a plugin someone installs and a plugin in the tree are read the same way.
