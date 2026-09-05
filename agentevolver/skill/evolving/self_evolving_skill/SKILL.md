---
name: self_evolving_skill
description: When to evolve the framework's own components, and the loop that does it. Use whenever a task is blocked or degraded by a missing or weak capability, when a sub-agent repeatedly fails for a fixable reason, or when the user asks to create, improve or evaluate a component. Covers the decide → generate/optimize → evaluate → adopt-or-roll-back loop, the enable_evolving gate, and how to read an evaluation. The how-to for each of the eight component types lives in generate_skill, optimize_skill and evaluate_skill, which the three agents read. NOT for the user's own deliverable work.
version: 2.1.0
license: N/A
type: [orchestrator]
category: meta
requirements: [cpu]
metadata: {}
---

# Self-Evolving

**When** to change the framework's own components, and **which loop** to run. This is an
orchestrator's document. *How* to write one is the three worker skills — `generate_skill`,
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
  There is no candidate pool and no promotion step, so a change is provisional until you have
  evaluated it.
- **The decision is yours.** Nothing scores a report and promotes or rolls back for you. You
  read the evaluation and decide, in your own reasoning, explicitly.

## What can be evolved

Eight types: `tool`, `skill`, `agent` (including its prompt), `connector`, `environment`,
`memory`, `workflow`, `plugin`. Every dispatch names one as `target_type` and the component
as `target_name` — a generate run names what it is about to create.

## When to evolve

The shared `evolution_rules` system-prompt module owns the detection policy. It applies to
ordinary tasks without any task instruction to evolve. This skill explains how to investigate
and close a detected opportunity; task wording or a component quota is not execution evidence.

**The question at a meaningful feedback or verification boundary: fix this deliverable,
recover infrastructure, reuse something available, or improve a reusable capability?**

> *If I re-dispatch the same agent with "fix X", will it plausibly succeed with the
> capabilities it already has?*
> **Yes → continue**, unless measured repetition makes a reusable improvement worthwhile.
> **No, it structurally lacks the means and will fail the same way → evolve.**

"Measured repetition" is not a recollection: it is something you recorded in durable project
memory and have now hit again. Your conversation starts empty every turn, so a second occurrence
is only citable if the first one was written down.

Evolution targets a capability defect, never a one-off weak attempt, and needs a real
**observed** signal — one of:

1. **Missing capability** — the task needs an operation no tool, skill or connector provides,
   and retrying cannot work → **generate** it. *The deliverable needs real product images and
   there is no media-search tool.* "The agent did not think to do it" is not a missing
   capability.

   Missing is always **missing for whoever has to do it**. A general-purpose shell counts as
   providing the operation — for whoever holds the shell. If the consumer is an agent whose
   roster does not include one, an operation reachable from a shell somewhere else is not
   reachable by them, and a script you can run yourself does not close their gap. Name the
   consumer before deciding; the answer changes with it.

   A shell usually closes an operation's availability gap for its holder. It does not
   establish that repeatedly writing the same script is economical; assess that separately
   under repeated cost, rather than calling a possible operation impossible.
2. **Recurring structural failure** — the same fault appears **≥2×** despite corrective
   guidance, so it is in a prompt or a tool rather than in the attempt → **optimize** it, or
   **generate** a skill that encodes the fix.

   The count is over *corrected attempts*: two occurrences separated by guidance that named
   the fault and was acted on. That separation is the whole test — it is what distinguishes a
   structural fault from one bad attempt. Re-dispatching a sub-agent is one way to produce a
   corrected attempt; it is not the only one, and a run that never dispatches twice can still
   have two of them. Identify what a corrected attempt is in the work you are actually doing,
   and count those.

   Retries inside one attempt do not count, however many there are. Neither does the same
   fault seen twice without anything having been done about it in between.

3. **Quality ceiling from a missing method** — output is *systematically* below bar on a
   dimension **you have measured**, because the agent has no method to do better and per-task
   instruction will not close the gap → **generate** a skill carrying the methodology. If you
   cannot name the measurement, you have signal-gathering to do, not a ceiling.

4. **Repeated cost or inconsistency** — comparable work has actually repeated, with measured
   time, token, correction, or error costs. Name upcoming consumers and compare direct work
   with the smallest reusable alternative, including creation, evaluation, integration and
   maintenance. Repetition alone is insufficient; the expected saving must justify the change.

Use evidence already produced by normal actions, feedback and checks. Verify a suspected gap
with a bounded check when necessary, not a separate model call or an elaborate mandatory
audit every step. Keep only a concise signal, correction and decision note in the existing
work record; preserve unresolved recurring signals through compaction. No qualifying signal
means ordinary task work, not an obligation to invent a defect before finishing.

**Check the cheap fixes first.** Most "missing capability" is really "not wired in":

- Is a suitable component already registered but **not in the roster or allowlist**? Add it
  rather than generating a duplicate, but only through authorized grants; isolation is not a
  defect to remove. Missing credentials, permission denials and provider outages alone are
  recovery or escalation issues, not capability defects.
- Did the sub-agent **ignore a skill it should have used**? Re-dispatch telling it to invoke
  that skill. A listed-but-unused methodology skill is the single most common cause of weak
  output.

**Do not evolve** on a first-time fixable defect (→ continue), a one-off transient error
(→ retry), when the budget is TIGHT or CRITICAL (→ finish the task), or to "improve" a frozen
built-in (→ generate an `extension/` component instead).

## The evolvability gate

Every component carries `enable_evolving`, and it is enforced in code: an optimize run that
tries to overwrite a frozen component is **blocked at registration**.

- **Built-ins are frozen.** Do not try to optimize one — the write is refused. If a built-in is
  inadequate, generate a new component in `extension/`.
- **Generated components are evolvable**, so a later round can improve them.
- **Always `inspect_tool` first**, with `capability_type` and the target name, to read
  `enable_evolving` and whether the target is even
  registered. Frozen means take the generate path, not the optimize path.

## The loop

1. **Assess** — `inspect_tool` the target: registered? evolvable? source path? Decide generate
   (missing, or a frozen built-in) versus optimize (exists and evolvable).
2. **Change** — dispatch `generate_agent` or `optimize_agent` with `target_type` and
   `target_name`. It authors the files under `extension/` and registers them.
3. **Evaluate** — dispatch `evaluate_agent` for the exact candidate version. Compare its
   behavior against the observed baseline and an independent case; check regressions and cost.
   Use a bounded consumer with only the necessary permissions, not unrelated live participants.
4. **Check it helped** — read the scored report against the defect that justified the change.
   Does it say the thing that was wrong is now right? That is the bar.
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

Typical shape: round N generate|optimize → round N+1 evaluate → round N+2 decide.

One component change per optimize step, so the evaluation can attribute the effect.
