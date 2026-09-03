---
name: self_evolving_skill
description: When to evolve the framework's own components, and the loop that does it. Use whenever a task is blocked or degraded by a missing or weak capability, when a sub-agent repeatedly fails for a fixable reason, or when the user asks to create, improve or evaluate a component. Covers the decide → generate/optimize → evaluate → adopt-or-roll-back loop, the enable_evolving gate, and how to read an evaluation. The how-to for each of the eight component types lives in generate_skill, optimize_skill and evaluate_skill, which the three agents read. NOT for the user's own deliverable work.
version: 2.0.1
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
  is done by `code_agent` / `general_agent`. *Self-evolution* changes the framework's own
  components and is done by `generate_agent` / `optimize_agent` / `evaluate_agent`. Evolution
  serves the task; it is never the deliverable unless the user asked for it.
- **Register-is-live.** A generated or optimized component becomes the active version the
  moment it registers — visible to the *next* sub-agent dispatched, not one already running.
  There is no candidate pool and no promotion step, so a change is provisional until you have
  evaluated it.
- **The decision is yours.** Nothing scores a report and promotes or rolls back for you. You
  read the evaluation and decide, in your own reasoning, explicitly.

## What can be evolved

Eight types: `tool`, `skill`, `agent` (including its prompt), `connector`, `environment`,
`memory`, `workflow`, `plugin`. Every dispatch names one as `target_type` and the component
as `target_name` — a generate run names what it is about to create.

## When to evolve

**The question every defect forces: fix *this deliverable*, or evolve a *capability*?**

> *If I re-dispatch the same agent with "fix X", will it plausibly succeed with the
> capabilities it already has?*
> **Yes → continue** (redo the work — this is the default).
> **No, it structurally lacks the means and will fail the same way → evolve.**

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

   The converse is the more common trap: if *you* are the consumer and you hold a shell, you
   almost certainly have no missing capability — you have work to do. Write the script.
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

**First, get a signal at all.** Every rule above needs an observed defect, and on an autonomous
task nobody hands you one. The absence of visible defects is not evidence the work is good — it
is usually evidence you have not looked. Make the work observable first (a check that can fail,
a reproduction, a reviewer pass); until one has run, the move is `continue`, never `evolve` and
never `done`.

**Check the cheap fixes first.** Most "missing capability" is really "not wired in":

- Is a suitable component already registered but **not in the roster or allowlist**? Add it
  rather than generating a duplicate.
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
- **Always `inspect_<type>` first** to read `enable_evolving`, and whether the target is even
  registered. Frozen means take the generate path, not the optimize path.

## The loop

1. **Assess** — `inspect_<type>` the target: registered? evolvable? source path? Decide generate
   (missing, or a frozen built-in) versus optimize (exists and evolvable).
2. **Change** — dispatch `generate_agent` or `optimize_agent` with `target_type` and
   `target_name`. It authors the files under `extension/` and registers them.
3. **Evaluate** — dispatch `evaluate_agent` for a scored, per-dimension report.
4. **Check it helped** — read the scored report against the defect that justified the change.
   Does it say the thing that was wrong is now right? That is the bar.
5. **Decide, and own it** — from the scored report, plus an independent `reviewer_agent` pass
   for a substantial change:
   - **Helped** → keep it (it is already live) and continue the user task using it.
   - **No better, or regressed** → you must not leave it live. Register-is-live means a bad
     change is already active, so rolling back is required rather than optional:
     `adoption_tool` `rollback` an optimized component to its previous version
     (`list_versions` first), or `unload` a brand-new one that has no prior version. Then
     re-dispatch with the failure evidence, or regenerate.
   - **Never evaluated at all** → also roll back. Not because the change is presumed bad, but
     because nothing looked: an unexamined component is live in the run and in every run
     after it. Evaluating is one dispatch; do that instead of skipping to a decision.
6. **Record** — state what you evolved, the score, the decision, and why.

Typical shape: round N generate|optimize → round N+1 evaluate → round N+2 decide.

One component change per optimize step, so the evaluation can attribute the effect.
