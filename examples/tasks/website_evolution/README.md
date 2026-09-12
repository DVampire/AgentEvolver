# Website self-evolution scenarios

One Website Builder owns design, implementation, native browser experience, creative critique,
and capability evolution. No persona files, user agents, acceptance agents or subscribers are
created. `scenario.html` contains product requirements; optional `materials/` supplies public
creative inputs and documented source requests. `experiment.json` selects the runtime profile,
declares material attachments and specifies evolution coverage separately from the product.

The launcher only loads that product brief and adds structured experiment settings.
Behavior belongs to the WebsiteBuilder prompt and frontend/testing skills; capability
evolution follows the shared `prompt/module/evolution_rules.html` and self-evolving skill.
There is no second behavioral prompt embedded in the launcher.

| Directory | Product intent |
| --- | --- |
| `arkbound_game/` | A 3D ocean adventure with branching consequences, functional vessel construction and a playable expedition studio using retrieved visual resources. |
| `commonspace_forum/` | A connected community workspace: discover and follow projects, discuss linked materials, compare proposals, coordinate actions and return to relevant changes. Includes reversible personal comparisons and local fictional materials. |
| `lumen_museum/` | Connected experiments, evidence notebooks and an editable curatorial workbench that turns new scholarly source packets into runnable inquiries. |
| `orbital_simulator/` | A 3D universe with physical frames, real numerical and imagery sources, and reconstructable investigations that test competing explanations. |

## Multiple entity types within each task

Arkbound, Lumen and Orbital each request verified `skill`, `agent` and `connector` improvements
in the same run. They are not three separate single-type demonstrations. Tools, workflows and
other component types remain available when a demonstrated need warrants them.

| Task | Connected opportunity for Connector → Agent → Skill | Changed-input follow-up |
| --- | --- | --- |
| Arkbound | Retrieve compatible visual resources → design a constrained branching expedition → apply a reusable play/design review method and publish playable content. | Revise cargo and return routes, halve the measured texture budget, preserve landmarks and earlier saves. |
| Lumen | Resolve a real source packet → reason about evidence and design an inquiry → apply a reusable experiment/explanation review method and publish a runnable route. | Missing source content, duplicated evaluation items, a shorter keyboard-only visit; preserve uncertainty and prior notebooks. |
| Orbital | Retrieve source states and imagery → design a discriminating investigation → apply a reusable scientific/visual verification method and reproduce it in the scene. | Different body, units and interval, offline replay and a deliberately exaggerated display; preserve physical measurements and earlier comparisons. |

These are opportunity hypotheses, not prebuilt components or guaranteed positive results.
The builder chooses component names, contracts and division of work from actual experience.
Each contribution needs a baseline comparison, independent case and post-adoption consumer;
the full product journey must also work with their combined results. A registered wrapper,
an unused skill or a hard-coded generated answer does not establish these outcomes.

After the first working chain, apply the changed input to the **kept versions first**. Diagnose
the weak layer, optimize its existing component, evaluate the new exact version and replay
downstream regressions. New names or version numbers do not prove optimization. If the kept
method still works, record successful reuse; do not manufacture a failure or replace it just
to increase the component count. Keep lineage and measured quality/cost in the plan records.

## Run

```bash
python examples/run_website_evolution_demo.py --scenario-dir examples/tasks/website_evolution/lumen_museum
python examples/run_website_evolution_demo.py --scenario-dir examples/tasks/website_evolution/commonspace_forum
```

The default scenario is `arkbound_game`; the default model is `llm_hub/gpt-6-astra`.
The three multi-entity scenarios select `configs/website_evolution_multientity.py` automatically.
It mounts a bounded `general_agent` for fresh reasoning baselines and lets the builder invoke
newly registered agents. It does not pre-create domain specialists. Baseline consumers have
no environments or product-editing tools; they return results through `done_tool`. The builder
performs the browser experience, evaluates the candidate and incorporates its result.
Keep baseline and candidate inputs, model, capabilities and budgets comparable. Their source
packets and returned designs may be passed by the builder; the website does not need a live
LLM for every interaction. Commonspace retains the original single-agent profile.

`--site-brief` overrides the product brief. `--model` (or
`--builder-model`) selects the builder's vision-capable route. `--config` overrides the selected
profile; a profile unable to invoke a required type cannot fulfil that experiment. If overriding
the comparison model, set `general_agent.model_name` through `--cfg-options` as well and use
that same model for candidate comparisons. Persona, user-model and acceptance-model flags
have been removed. Existing sessions retain their staged task. Declared materials are staged
through ordinary task attachments; the manifest supplies their actual paths without adding
their full contents to the launcher's task text.

## One builder, separate phases

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

Visual improvement is required: inspect real desktop/narrow-screen discovery, reading and
participation views; identify a concrete visual weakness, implement a purposeful change,
then compare equivalent before/after screenshots and replay the affected journey. Technical
checks, feature count or a theme swap alone do not prove the result became more beautiful.
The demo supplies clean browser screenshots; textual element coordinates remain available.

The builder uses the same plan module as GameBuilder, with `use_plan=True` and automatic
planning. `--plan-mode plan` instead requests an approval gate; it is not needed to use a
living plan. The frontend skill's `references/planning.md` guides website records. Keep
`index.md` concise (2,000 characters total) and link the detailed plan, visual review and
system evaluation records. Only the index is projected into live context; extra document
names and layout are chosen by the agent. Update after meaningful work, not every tool call.

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
baseline/candidate fixtures can be run by the builder. The multi-entity profile also supports
fresh model consumers; the single-agent profile cannot prove comparisons that require them.

`evolution.required_modules` adds type-specific coverage to the same receipt audit. Every
listed family must have an active version registered by this task, evaluated, kept and actually
used. Missing families are named in status and cannot be satisfied by extra Tools. An optimized
version of an existing component qualifies through this same lifecycle; registering a newer
version invalidates the older active-version evidence until the new version is verified.

Runtime checks call provenance, lifecycle and active version; semantic benefit remains an
explicit, reviewable evaluation judgment. Missing evidence makes completion unsuccessful,
including text-only endings. A rejected experiment proves the trigger ran, not an improvement.
Do not invent gaps or retain failed candidates to pass. Report concrete blockers and preserve
partial website results. Configuration tests check assembly, not a live successful evolution.
