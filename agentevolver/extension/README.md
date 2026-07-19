---
name: extension
description: "Manages generated extension manifests and their promotion into the active framework. Promotion is journaled and guarded by replay-based smoke checks."
version: 0.1.0
type: module
category: extension
requirements: []
metadata:
  tracks_package_version: true
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
