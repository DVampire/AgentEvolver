---
name: evaluate_skill
description: How to evaluate an existing component of any of the eight types this framework can build — tool, skill, agent, connector, environment, memory, workflow, plugin. Use when scoring a component's quality and producing findings, changing nothing. Carries one reference file per type saying what that type is, what its contract is, and how to exercise it, plus the five scoring dimensions every type is judged on. Read by evaluate_agent.
version: 1.1.0
license: N/A
type: [worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Evaluate

How to judge one existing component. `target_type` says which kind, `target_name` which one;
read that type's file first — it says what the contract is, so a compliance finding is against
the real contract rather than a guess.

| `target_type` | read |
|---|---|
| `tool` | `references/tool.md` |
| `skill` | `references/skill.md` |
| `agent` | `references/agent.md` |
| `connector` | `references/connector.md` |
| `environment` | `references/environment.md` |
| `memory` | `references/memory.md` |
| `workflow` | `references/workflow.md` |
| `plugin` | `references/plugin.md` |

## Evaluating changes nothing

You never edit the thing you are judging — a grader with write access can resolve a bad grade by
editing what it graded, so this run is read-only. The one exception is recording your own verdict
through `adoption_tool`.

**Read the exact candidate version once**, then record the version and source path in the
existing work record. Reuse source reads and scores only for that version in this evaluation;
if the target changes, inspect and evaluate the new version rather than recycling old evidence.

**Then exercise it.** The evidence is what you observed, not what you expect from reading. How to
exercise each type is in its file; in general, call a tool directly, invoke a skill and apply
its method to a representative case, dispatch an agent on a small task, or make an authorized
read-only call against a connector or environment. Reading instructions alone does not prove
that a skill improves outcomes. If necessary execution or isolation is unavailable, report
inconclusive rather than bypassing permissions.

**Match verification to the change.** For a small method change, compare one representative
baseline/candidate case and one independent reuse or regression case. The baseline may already
work: test the claimed improvement, not just whether a defect disappears. Expand coverage for
broader, stateful, permission-sensitive or externally mutating changes to the affected operations,
isolation, recovery and relevant regressions. Never omit required safety checks. State the tested
scope and untested limits; sampled success does not establish that every operation works.

**No standalone scripts.** Do not write an eval script for `bash_tool` or `code_interpreter_tool`
— those run in a fresh process where the target is not registered, so the run proves nothing.
Exercise the component through the framework.

## The five dimensions

Each scored 0–20, total 100.

**Correctness (20)** — does it produce the expected result for valid input? Test the scoped
operations with known inputs; for a full component review, cover every supported operation.
20 = all scoped cases pass; −4 per failing case. Disclose coverage with the score.

**Robustness (20)** — does invalid input fail gracefully rather than crash? Test missing
arguments, wrong types, out-of-range values. Expected: a failure *returned*, not an unhandled
exception — a crash surfaces to the caller as an action error. 20 = all handled; −5 per unhandled
exception.

**Interface compliance (20)** — does it honour the contract its type declares? The type's file
names that contract. Deduct per missing or malformed element.

**Quality (20)** — is the source readable and free of obvious defects? Judge from reading alone:
clear naming, no dead code, appropriate error handling. Syntax is implicitly valid — it would not
be registered otherwise. Deduct per issue found.

**Performance (20)** — does it respond acceptably? Judge from the source and your own calls; flag
blocking I/O or heavy work in a hot path. A network call with no timeout is a finding regardless
of how fast it was when you tried it. Precise timing is not required.

Report per-dimension scores with the evidence behind each, and concrete suggestions an optimize
run could act on.
