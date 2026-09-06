---
name: self_evolving_skill
description: Turn a concrete reusable improvement opportunity into a small verified framework capability change. Use for learning from a first correction or success, expected reuse, a better quality/cost/reliability method even when the task already works, a missing capability, or an explicit component request. Repeated failure is not required. Covers inspect → generate/optimize → evaluate → keep/rollback/unload across all eight component types. NOT for ordinary edits to the user's deliverable.
version: 2.2.0
license: N/A
type: [orchestrator]
category: meta
requirements: [cpu]
metadata: {}
---

# Self-Evolving

**How** to investigate and close an opportunity identified by the shared `evolution_rules`.
This is an orchestrator's document. *How* to write a component is in the three worker skills — `generate_skill`,
`optimize_skill`, `evaluate_skill` — each covering all eight component types, each read by
the one agent that does that job.

## What this is

- **Two directions, never confuse them.** *User work* — write the app, answer the question —
  is done directly by the responsible agent or a suitable bounded worker. *Self-evolution* changes the framework's own
  components and is done by `generate_agent` / `optimize_agent` / `evaluate_agent`. Evolution
  serves the task; it is never the deliverable unless the user asked for it.
- **Register-is-live.** A generated or optimized component becomes the active version the
  moment it registers. Callable rosters refresh according to each consumer's scope; this
  does not replace an already running Agent, Environment, or Memory instance.
  Staged artifacts pass registration checks and promotion, but this is not behavioral
  acceptance. Keep use bounded to validation until the exact version has been evaluated.
- **The decision is yours.** Nothing scores a report and promotes or rolls back for you. You
  read the evaluation and decide, in your own reasoning, explicitly.

## What can be evolved

Eight types: `tool`, `skill`, `agent` (including its prompt), `connector`, `environment`,
`memory`, `workflow`, `plugin`. Every dispatch names one as `target_type` and the component
as `target_name` — a generate run names what it is about to create.

## Prepare an opportunity

The shared `evolution_rules` system-prompt module owns the detection policy. It applies to
ordinary tasks without any task instruction to evolve. This skill explains how to investigate
and close a detected opportunity; task wording or a component quota is not execution evidence.

Do not apply a second, stricter trigger gate here. A first correction, successful discovery,
expected reuse, or better method can justify an experiment even if direct task work could
succeed. Repeated failure and a broken baseline are not required.

Prepare four short facts in the existing work record, then proceed to inspection:

- **Evidence:** the observation, feedback, or result that prompted the idea. Current
  conversation, tool results, checkpoints, traces, and durable notes are valid sources;
  writing a memory file is not a prerequisite. Cite actual sources when claiming repetition.
- **Use:** the consumer and a concrete reuse case beyond this single product edit. Label
  future uses as expected, not already observed. A shell can provide an operation without
  providing the best reusable method; do not mislabel possible operations as impossible.
- **Benefit and check:** the quality, effort, reliability, or creative outcome to improve,
  the baseline comparison, and a bounded way to test it. Predicted savings are hypotheses,
  not measured results. A qualitative criterion is acceptable if the evaluator can compare
  concrete outputs against it.
- **Scope and budget:** the smallest useful component change and enough remaining resources
  for construction, evaluation, integration or rollback, and task completion. Follow the
  shared policy's budget rules; no extra waiting period or failure count applies here.

Inspect existing components and reuse a suitable available capability rather than making a
duplicate. Respect consumer grants: missing credentials or permissions are not authorization
to bypass isolation. A transient error alone calls for recovery; an evidenced reusable recovery
method may be an improvement opportunity. Do not confuse fixing product code with evolving
the framework capability that produces or checks it.

Keep this decision in the normal action loop, not a per-step audit or extra model request.
Preserve useful unresolved opportunities through compaction; use authorized durable memory
for knowledge needed by later independent threads. No opportunity means ordinary task work,
not an obligation to manufacture an evolution or cover every entity type.

## The evolvability gate

Every component carries `enable_evolving`, and it is enforced in code: an optimize run that
tries to overwrite a frozen component is **blocked at registration**.

- **Inspect the actual flag.** Do not infer mutability from a built-in or generated name.
  If the target is frozen, generate an alternative in `extension/` rather than overwriting it.
- **Always `inspect_tool` first**, with `capability_type` and the target name, to read
  `enable_evolving` and whether the target is even
  registered. Frozen means take the generate path, not the optimize path.

## The loop

1. **Assess** — `inspect_tool` the target: registered? evolvable? source path? Decide generate
   (missing, or a frozen target) versus optimize (exists and evolvable). Preserve baseline
   evidence and the prior version before registering a change, so comparison and rollback
   do not depend on reconstructing an overwritten baseline.
2. **Change** — dispatch `generate_agent` or `optimize_agent` with `target_type` and
   `target_name`. It authors the files under `extension/` and registers them.
3. **Evaluate** — dispatch `evaluate_agent` for the exact candidate version. Compare its
   behavior against the observed baseline and an independent case; check regressions and cost.
   For a small method change, use one representative baseline/candidate comparison and one
   independent reuse or regression case. Expand coverage for broader, stateful, permission-
   sensitive or externally mutating changes; never shrink required safety checks to save cost.
   Use a bounded consumer with only the necessary permissions, not unrelated live participants.
   If meaningful exercise is unavailable, the verdict is inconclusive, not a pass from reading.
4. **Check it helped** — compare observed results with the claimed benefit, even when the
   baseline already works. A higher score alone does not demonstrate that benefit.
5. **Decide, and own it** — use the observed comparison, not the score alone. For a substantial
   change, obtain proportionate independent verification through an available evaluator:
   - **Helped** → keep it (it is already live) and continue the user task using it.
   - **No better, or regressed** → you must not leave it live. Register-is-live means a bad
     change is already active, so rolling back is required rather than optional:
     `adoption_tool` `rollback` an optimized component to its previous version
     (`list_versions` first), or `unload` a brand-new one that has no prior version. Then
     re-dispatch with the failure evidence, or regenerate.
   - **Never evaluated at all** → also roll back. Not because the change is presumed bad, but
     because nothing looked: an unexamined component is live in the run and in every run
     after it. Evaluating is one dispatch; do that instead of skipping to a decision.
6. **Integrate and record** — use `adoption_tool` to record the decision and verify that the
   intended consumer actually uses the adopted version on subsequent real work. If changing
   Agent, Environment or Memory, use a supported handoff or a fresh bounded consumer; a registry
   entry alone is not an instance migration. Record the evidence, version, decision, actual
   consumer and outcome. Product preference storage is not framework Memory evolution.

Complete generate/optimize → evaluate → decide in one bounded improvement cycle. These are
action steps, not separate product releases; do not wait for another release to evaluate.

One component change per optimize step, so the evaluation can attribute the effect.
