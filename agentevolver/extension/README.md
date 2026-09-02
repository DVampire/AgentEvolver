---
name: extension
description: "Manages generated extension manifests and their validated promotion into the active framework."
version: 1.0.0
type: module
category: extension
requirements: []
metadata: {}
---
# Extension

Manages generated extension manifests and their promotion into the active framework.
Admission validates registry and model-facing schema contracts without calling an LLM;
functional quality is measured by evaluation and rollout.

| File | Responsibility |
|---|---|
| `types.py` | Manifest and component contracts |
| `server.py` | Transactional registration, deterministic admission, and promotion facade |
| `journal.py` | Recoverable change journal |
| `rollout.py` | Measured shadow/canary activation and rollback |

Extension coordinates installation; the owning Tool, Skill, Agent, or Workflow Manager
remains the source of truth after registration.

## Generated components never land in `agentevolver/`

`agentevolver/` is the framework and stays immutable; everything the system writes about
itself goes in this tree. Authoring writes one flat active file — `extension/tool/<name>.py`,
`extension/agent/<name>.py` beside `extension/prompt/<name>.html`,
`extension/skill/<name>/SKILL.md`, `extension/connector/<name>/CONNECTOR.md` — and
`add_component` registers it, archives the version under `.versions/`, and records the
active one in `manifest.json`.

**There is no `__init__.py` to edit here.** Loading is a directory scan plus a dynamic
import, so an extension that adds itself to an `__init__.py` is editing framework source to
no effect. That is the one difference from a hand-written built-in, which *does* need its
import line in the module's `default/__init__.py` to register at all.

The tree carries nine module kinds: `tool`, `agent`, `prompt`, `skill`, `environment`,
`connector`, `workflow`, `memory` and `plugin`. A `plugin/<name>/` component has the same
shape as a built-in one — `plugin.py` beside `PLUGIN.md`, with `tools/` and `resources/` —
so a plugin someone installs and a plugin in the tree are read the same way.
