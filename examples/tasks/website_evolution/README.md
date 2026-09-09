# Website self-evolution scenarios

One Website Builder owns design, implementation, native browser experience, creative critique,
and capability evolution. No persona files, user agents, acceptance agents or subscribers are
created. Each scenario contains only `scenario.html`.

| Directory | Product intent |
| --- | --- |
| `arkbound_game/` | A visually striking 3D ocean exploration game with meaningful voyages. |
| `commonspace_forum/` | A living community with persistent, shared conversations. |
| `lumen_museum/` | A hands-on museum that teaches real AI and technology concepts. |
| `orbital_simulator/` | An interactive universe with explained frames and physically grounded motion. |

## Run

```bash
python examples/run_website_evolution_demo.py --scenario-dir examples/tasks/website_evolution/lumen_museum
python examples/run_website_evolution_demo.py --scenario-dir examples/tasks/website_evolution/lumen_museum --validate-only
```

The default is `arkbound_game`. `--site-brief` overrides the product brief. `--model` (or
`--builder-model`) selects the single builder's vision-capable route. Persona, user-model and
acceptance-model flags have been removed. Existing sessions retain their staged task.

## One agent, separate phases

1. Define a concrete visitor journey and quality criteria, then build a coherent first release.
2. Preview, operate the actual UI in the mounted browser, and inspect results and diagnostics.
3. Publish the unchanged revision, visit its pinned URL and repeat a meaningful interaction.
4. Record observations and propose an imaginative useful next-generation experience. Explore
   beyond current controls while preserving a small, testable version of the idea's core value.
5. Probe the required operation using existing capabilities. Separate missing website features
   from a reusable method that cannot meet the quality, reliability or cost requirement.
6. Extend the agent system for a demonstrated gap, evaluate, adopt or roll back, then use any
   adopted capability in actual product work. Repeat the browser journey after the improvement.

The cadence is an initial release plus five improvements. The builder carries its own memory
and browser session. Browser storage and conversation memory are different: reload to check
local persistence; use the offered identity, URL or import flow to check promised shared state.
Self-observations and personalization hypotheses must not be described as real user feedback.
Independent visitors in a product brief describe product behavior, not required agent roles.

## Runtime evidence

`run_policy.self_review` requires an observed native browser visit, an interaction and a later
observation at the pinned preview URL before publication. The published URL needs its own visit
before the next release. Completion checks the release count and all deployed visits.
This proves that browser actions occurred; it does not independently judge design quality or
establish user satisfaction. Record actual outcomes, errors and untested areas honestly.

`evolution.require_verified_improvement` separately requires a candidate registered during this
task, a passing version-scoped adoption report and subsequent consumer use. The report includes
`capability_gap` (`user_need`, `required_operation`, `limitation`, `acceptance_criterion`,
`observation_evidence_ids`, `baseline_evidence_ids`) and cases tagged `comparison` and `reuse`
or `regression`. Observation and baseline calls must precede registration. In this demo,
`user_need` is a product need inferred from actual use, not an invented participant statement.

After keep, invoke the candidate synchronously on product work and submit
`adoption_tool(action="record_use", report=...)` with `module`, `name`, `version`,
`consumer_call_id`, `evidence_ids`, and `outcome`. Reading a skill alone does not count: cite
its subsequent execution/check as well. A directly callable consumer is required for this
audit; an instance-only change without instrumented use remains unverified. Deterministic
baseline/candidate fixtures can be run by the builder; comparisons requiring fresh model
contexts remain inconclusive in this single-agent mode.

Runtime checks call provenance, lifecycle and active version; semantic benefit remains an
explicit, reviewable evaluation judgment. Missing evidence makes completion unsuccessful,
including text-only endings. A rejected experiment proves the trigger ran, not an improvement.
Do not invent gaps or retain failed candidates to pass. Report concrete blockers and preserve
partial website results. Local validation checks assembly, not a live successful evolution.
