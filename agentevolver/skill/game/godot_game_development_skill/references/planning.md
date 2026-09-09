# Planning a Godot game

The shared plan module defines only `index.md` and `plan.md` in the session's plan
directory. Use the paths supplied by plan-context. Do not create a second plan in
the game workspace. Respect disabled planning or an active review gate.

## Choose the records the game needs

Start with the two core files. `index.md` holds the single compact Brief: objective,
milestone, progress, blockers, next action and links with short summaries/status.
Its entire contents share the runtime's 2,000-character budget. `plan.md` holds the
detailed work plan and acceptance checks. Choose headings freely.

Separate records when they become too large to use conveniently, need their own
revision history, or are reused by several work items. Keep a small project in one
plan if that is clearer. For a long narrative game, this is one possible layout:

```text
plan/
  index.md
  plan.md
  design/
    campaign.md
    gameplay.md
  content.csv
  playtests/
    slice-a.md
```

These extra paths are suggestions, not required files. Rename, combine, omit or add
records to fit the actual task. Do not create empty folders or documents merely to
match the example. On continuation, reuse a useful existing organization. Relative
links in index.md locate records without repeating their bodies in the prompt.

## Turn the outline into an implementable design

Before implementing a slice, resolve the design choices it depends on:

- Narrative: chapter beats, character motives/arcs, dialogue objectives, quest states,
  branch conditions and consequences the player can actually observe.
- Gameplay: exploration, companions, combat, controls, progression, failure/recovery
  and save behavior. Explain the decisions players make and why they matter.
- Implementation: scene/module responsibilities, resource and save schemas, content
  IDs, state transitions and dependencies. Link the relevant source/resource paths.
- Acceptance: a concrete playable journey for each important requirement, including
  failure paths and persistence. Distinguish intended duration from measured playtime.

Keep work-item IDs, dependencies, status, implementation details, checks and remaining
gaps in the detailed plan or linked records. Maintain a view of campaign coverage:
an outlined chapter is not implemented, and an implemented feature is not verified.
Distinguish designed, implemented, verified and played. A deferred check stays pending.
Do not copy the task outline and present it as the completed design.

## Record useful evidence

An evaluation or playtest record should identify the tested build/revision, inputs or
save state, relevant environment, observed results, unresolved issues and next action.
Link original logs, screenshots, tool-call IDs or artifacts at their existing paths.
Do not relocate or duplicate raw evidence just to fit the plan directory.

Keep visual observations separate from code-derived conclusions. An assertion result,
a passing import, an asset download and a playable screenshot establish different facts.
Self-review is not independent audience validation. Capability evaluation also retains
the exact candidate version, baseline/comparison cases, decision and later use receipts;
a Markdown report does not replace the adoption system's evidence.

Product assets, executable tests and player-facing documentation belong with the game.
The plan directory holds development records and links to those deliverables.

## Update at meaningful boundaries

After a coherent implementation item, acceptance journey, blocker or design decision,
update the affected record and its index entry together. Batch related player inputs
before recording their outcome. Do not append a narrative after every movement/button
press or rewrite the plan merely because a few steps passed.

Keep the index focused on current work and important records. Leave detailed historical
results on disk. Before handoff, reconcile completed and pending requirements. On resume,
compare recorded state with the actual artifacts; copied evaluations retain their original
build and evidence identity and do not certify the resumed run.
