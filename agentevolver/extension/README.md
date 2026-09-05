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

## Version-scoped evaluation evidence

All eight evolvable families use `ComponentEvaluation`: candidate identity/version,
baseline, verdict, and cases with expected/observed results and actual evaluator call
IDs. `EvaluateAgent` binds the initial candidate version and checks it again at the end;
missing evidence or a changed version produces no adoptable report. This is a
structured evaluator judgment, not independently proven semantic correctness.

`adoption_tool.record_decision` accepts only the caller's completed EvaluateAgent child.
The extension manager persists the decision in `.evaluations.json`; `keep` requires
a passing report for the exact active, archived version. This contract is independent
of website releases and task-specific runtime extras. Recording a rollback/unload
decision does not execute it: use the corresponding operation explicitly.

All eight families pass loading/construction/schema admission in a separate Linux
bubblewrap interpreter before live import. The probe has a read-only code snapshot,
isolated network and temporary writable directories; inherited environment credentials
are excluded. Explicit construction config must not contain secrets. Missing isolation
fails closed. Probe output and content digests are retained under `.checked/`; changed
or injected cache files require rechecking, and symlink roots are rejected.

Version archives preserve admitted bytes rather than a later edit of the authoring file.
Reusing a version number with different content is rejected. A failed cold-start load
does not erase the accepted manifest pointer or trigger an unapproved directory rescan.

These are mandatory structural checks, **not functional-quality evaluation**. Candidates
can still become provisionally active after admission and before EvaluateAgent's functional
judgment. Mandatory isolated behavioral evaluation before activation for all eight families
remains incomplete. Existing measured tool rollout remains a separate mechanism.

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
