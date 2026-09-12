---
name: self_evolving_skill
description: Generate, optimize, repair and evaluate reusable framework capabilities across tool, skill, agent/prompt, connector, environment, memory, workflow and plugin. Use after a concrete improvement opportunity, component failure, first correction or discovery, expected reuse, or an explicit component request. Owns the shared inspect → author → register → evaluate → repair or adopt → use loop. Repeated failure is not required. NOT for ordinary edits to the user's deliverable.
version: 2.7.0
license: N/A
type: [orchestrator]
category: meta
requirements: [cpu]
metadata: {}
---

# Self-Evolving

The current agent owns generation, optimization, evaluation and repair of all eight
component families through this skill. Use the existing editing, inspection, invocation
and adoption tools in the normal action loop. There is no separate generate, optimize or
evaluate agent to dispatch, and a component error is feedback for this loop.

The shared `evolution_rules` prompt decides **when** to investigate an opportunity. This
skill defines **how** to complete it; domain skills supply domain methods and acceptance
criteria. Task briefs describe user outcomes. Do not duplicate this lifecycle in each
actor, domain skill or task, or add a second trigger gate here.

## Read the common procedure and the selected contract

Read [conventions.md](references/conventions.md) before authoring or evaluating a candidate.
It defines staging, registration, failure recovery and the evidence/decision contract for
every family. Then read only the selected type's reference and relevant template. Those
references add artifact and execution requirements; they do not replace the common loop.

| Family | Useful form | Type contract |
|---|---|---|
| `tool` | A bounded executable operation with explicit inputs and results | [Tool](references/tool/tool.md) |
| `skill` | A reusable procedure, decision method or review method, optionally with scripts | [Skill](references/skill/skill.md) |
| `agent` | Reasoning or specialist behavior; its prompt is part of this family | [Agent](references/agent/agent.md) |
| `connector` | Access to an external service or data source, including an authored local MCP server | [Connector](references/connector/connector.md) |
| `environment` | Stateful observation, interaction, simulation or execution | [Environment](references/environment/environment.md) |
| `memory` | Retention, retrieval and bounded presentation of experience | [Memory](references/memory/memory.md) |
| `workflow` | Reusable sequencing and coordination of capabilities | [Workflow](references/workflow/workflow.md) |
| `plugin` | A cohesive package of related capabilities | [Plugin](references/plugin/plugin.md) |

Choose the smallest form that addresses the reusable limitation and has a real consumer.
An existing component can be improved; creating another component is not the default.
A Skill need not become a Tool, and an Agent improvement need not create another agent.
Product source, preference storage and task reports are not framework capabilities.

Templates live under `references/<type>/template*`; support scripts under `scripts/<type>/`.
A `template-manifest.md` becomes the type's real manifest inside the new component. Do not
rename templates in this skill to `SKILL.md`: nested files with that name are discoverable.

## Establish the opportunity

Use the existing work record to preserve four short facts:

- **Evidence:** the observation, feedback, first correction or successful discovery that
  suggests a reusable improvement. Cite actual calls when claiming executed behavior.
- **Consumer:** the next operation and a different reuse case. Future uses are expected,
  not already observed. Confirm that the consumer can load and exercise the chosen family.
- **Benefit and acceptance:** the required operation and observable quality, reliability,
  effort or cost improvement, with a baseline and a bounded comparison. Qualitative
  criteria can compare concrete outputs. Predicted savings are hypotheses.
- **Scope and budget:** the smallest useful change, with resources for evaluation, repair
  or rollback, integration and the user task. Follow the shared prompt's budget policy.

Inspect the existing method and collect baseline evidence before registration. A missing
product feature does not establish a missing framework capability. A baseline can already
work while a better reusable method is worth testing. Available Bash is not proof that a
reliable method exists, nor is unfamiliarity proof that an operation is impossible.

Keep discovery and evaluation within authorized inputs and local checks. Do not inspect
hidden grading data or change the benchmark. No opportunity means ordinary task work;
never manufacture a gap or require repeated failure before investigating a real one.

## One lifecycle for all eight families

1. **Inspect and choose.** Use `inspect_tool` with `capability_type` and `name`; read the
   actual source, contract, version and `enable_evolving`. Optimize an existing evolvable
   component. For a missing component, generate one; for a frozen target, generate a
   suitable alternative under a new name. A frozen target does not end the user task.
   Preserve the prior version, baseline inputs, outputs and call IDs for comparison.
2. **Author in staging.** Use the available Bash/apply_patch tools to write the artifact
   required by its type. Implement the real operation, dependencies, effects and failure
   path. Keep public contracts compatible and avoid overwriting files still in use.
   Perform the type's load/construction checks; syntax alone is insufficient.
3. **Register.** Call `adoption_tool action=register` with `module`, `name` and the absolute
   `artifact_path`. Failed registration returns to authoring; it is not an adopted version.
   Preserve the returned `candidate_version`, `active_version` and rollout status. An
   active candidate is provisional until evaluated. Shadow/canary calls may still execute
   the baseline; attribute results to the version actually exercised. Registration neither
   grants permissions nor replaces running Agent, Environment or Memory instances.
4. **Evaluate the exact version.** Freeze candidate source and comparison inputs during
   this evaluation, then invoke the registered capability. Compare a representative
   baseline/candidate case and an independent reuse or regression case, expanding coverage
   for broader changes. Test the required successful operation as well as relevant failure
   handling; measure the claimed benefit and cost. Use isolated state for writes. Support
   scripts and fixtures help verification but do not replace native consumer evidence.
5. **Decide or repair.** Record the version-scoped `pass`, `fail` or `inconclusive` verdict
   through `adoption_tool action=record_decision`. Keep only a passing active version whose
   evidence supports the claimed benefit. For a failed or unassessed candidate, record the
   result and perform `rollback` to the prior good version, or `unload` if none exists.
   Read the failure, repair in staging and return to registration and evaluation. Recording
   a rollback/unload decision alone does not perform the operation.
6. **Use and verify.** Use the adopted version on subsequent real task work and check the
   consumer result. For a Skill, load it and execute its method; for an instance-bound
   component, use a supported fresh consumer or handoff. Record actual version and call
   evidence. If the task requires verified evolution, submit the additional `record_use`
   receipt described in the conventions. Registration or loading alone is not use.

Each edit returns through the same cycle. Never attribute old evidence to a repaired
version, or change source during its evaluation. If registration did not create a version,
keep the failed attempt in the work record and repair it without inventing a version or
submitting an adoption decision for it.

## Failure is work to diagnose

Registration, discovery, invocation and evaluation errors do not automatically end the
task. Read the concrete result and relevant source, distinguish an argument/setup mistake
from a component defect or external constraint, and follow the recovery table in the
conventions. Missing implementation, dependencies or incorrect schemas/effect declarations
are authoring work when repairable within the current scope. An MCP server can be authored
as part of a Connector, just as Python implementation belongs to a Tool.

Repair using the available tools, then retry the failed operation and a relevant regression.
Do not repeat unchanged failing calls, turn an error into a success-shaped report, or use
`done_tool` to avoid repairable work. For external access problems, investigate permitted
alternatives and continue independent work. A genuine prerequisite or exhausted budget can
leave a gap unresolved; preserve evidence and reconcile provisional changes before ending.

Keep the original acceptance criterion. Correctly rejecting unavailable input verifies a
failure path; it does not prove that the required successful operation works. A narrow
improvement can be valid, but cannot close a broader unmet requirement. Do not invent a pass
or keep a failing candidate to satisfy an entity count.

## Evaluation and continuing work

The current agent evaluates its work using executed evidence. It can run deterministic
fixtures and comparisons itself. Use an available authorized bounded consumer when the
claim requires a fresh model context, independent reasoning or instance construction;
do not assume `general_agent` or a reviewer exists. Never pretend to remove a learned skill
from the same continuing conversation. If the needed comparison cannot run, state the
missing evidence as inconclusive. Source review alone is not behavioral verification.

For several components, change one candidate at a time, link input/output artifacts and
consumer call IDs, evaluate each, then replay the complete consumer journey. On changed
inputs, optimize the weak layer against its kept version and preserve earlier successes as
regressions. Successful reuse needs no artificial version bump. Explicit
`evolution.required_modules` requirements are coverage to track, not permission to fabricate
gaps or disguise one family as another.

Keep a concise status and record path in the session's `plan/index.md` when planning is
enabled. Store detailed comparisons, errors, versions and consumer evidence under its plan
directory using the domain skill's layout; otherwise use the available durable work record.
Distinguish proposed, staged, registered, evaluated, adopted and used. Preserve unresolved
work through compaction and record decisions promptly. Advance the loop now rather than
leaving only a future plan entry. Close any delegated probes and reconcile provisional
changes before finishing; no extra per-step audit or model call is needed.
