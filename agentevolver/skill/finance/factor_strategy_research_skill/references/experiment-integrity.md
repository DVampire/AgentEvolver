# Executable research integrity

Read when building or repairing the two generated Environments. Copy and pin the linked
helpers with the components, import them in actual research actions, and bind their hashes
in engine identities. They supply mechanical checks, not factors, a backtester, statistical
qualification or enforcement against the agent editing its own code.

## One chronological plan

For a train/test study, discovery, hyperparameter/model choices, combination fitting and
selection all consume train. The agent chooses internal rolling windows using available
history, mechanism, outcome availability and statistical support. These windows are reused
research data; calling them out of sample does not make adaptive selection independent.

Construct the exchange calendar with `check_snapshot.expected_sessions`, verify the saved
training snapshot against it, and save that list. Then create a plan:

```json
{
  "schema": 1,
  "study_id": "the-exact-study-id",
  "train_sessions_sha256": "digest of the verified session list",
  "windows": [
    {"id": "agent-chosen-window-id", "fit": ["fit-start", "fit-cutoff"],
     "score": ["later-score-start", "score-end"], "gap_sessions": 0}
  ]
}
```

Dates above are placeholders, not a schedule; zero is the example gap's structural minimum,
not a recommended gap. Choose the actual gap and purge rows based on outcome availability.
Use any justified number or duration of windows. Nonoverlapping score windows can be pooled;
alternative overlapping schedules require separately named plans/scenarios.

Use [research_plan.py](../scripts/research_plan.py):

- `validate_plan(study, plan, sessions)` checks the train/test ordering, exact calendar
  binding, real session endpoints, each fit/score gap and score overlap. Both Environments,
  comparisons and reports consume these same window IDs and scored dates; do not hardcode
  three folds or named calendar years in downstream operations.
- `validate_research_dates(study, dates)` runs before ordinary factor materialization,
  fitting, simulation and comparison data reads. Validate all requested/loaded dates,
  including label endpoints, not only the chart's scored dates. Reject test in ordinary
  actions rather than silently clipping it or exposing it through diagnostics.
- `validate_fit(window, feature_dates, label_exit_dates)` checks the actual retained rows
  after purging. Use it for factor transforms AND combination/model fitting. Record the
  latest availability of every fitted target/input; unsupervised transforms use row
  availability. The plan check precedes this row check; neither proves an arbitrary
  implementation causal without prefix/future-perturbation fixtures.

```bash
python {skill_dir}/scripts/research_plan.py /absolute/study.json \
  /absolute/plan.json /absolute/train-sessions.json
```

Continuous-account scenarios separately declare start capital/state and model effective
dates. They use the same eligible fitting boundaries without manufacturing returns for gaps
in a statistical fold composite. New plans are new research assessments, not overwritten
evidence or an independent dataset. Historical three-split archives remain historical;
do not relabel their old results to look like newly evaluated train-only evidence.

## Exact parent and intervention

The executable strategy spec is authoritative. Numeric settings live in `parameters` or
an explicit combination/model configuration, not copied literals in descriptions. Build
controls by copying the exact parent, then applying declared edits. Display actual settings
from the structured spec; prose explains their meaning and references parameter names.

Use [strategy_spec.py](../scripts/strategy_spec.py):

1. Validate/hash both definitions and implementations. `execution_definition(spec)` extracts
   executable fields, including new configuration extensions. Runtime policies consume this
   configuration, never parse `design` or descriptions to recover numeric settings.
2. `revision_receipt(parent, candidate, declared_paths)` verifies the exact parent and full
   change set as JSON pointers. List changes such as `/parameters/entry`, `/factor_bindings`
   and `/implementation/sha256` explicitly. Undeclared changes and declared changes that did
   not occur are errors. Preserve its before/after values with the comparison.
3. Before applying an intervention, replay parent and the control wrapper with interventions
   disabled on identical saved inputs and parent settings. Pass dated target/rebalance and
   relevant policy-state records to `assert_noop_parity`. Include boundary cases reachable
   under the actual parent settings. Check every compared scope. Changing implementation
   hashes alone is not evidence that this parity check passed.
4. Apply the declared edits, then evaluate. A fixed-input intervention, a train-refitted
   reduced model and a joint mechanism revision answer different questions; label them
   accordingly. Several changes are allowed but cannot be interpreted as a single-factor
   contribution. Invalid controls do not establish the sign of any factor's contribution.

These checks cannot infer the semantics of arbitrary Python. Bind the replay inputs,
implementation/config hashes and parity receipt to the real comparison action; a separately
fabricated fixture trace cannot substitute for exercising its actual wrapper.

## Combination fitting is a strategy operation

Support candidate-specific combination logic and an optional `fit_combination` operation,
not just per-factor independent fits. A fixed rule legitimately records no combination fit.
A trained blend, interaction, conditional model or state transition records feature roles,
target and availability, preprocessing, model/parameter selection, training window and
serialized fitted-state hashes. Exact factor versions and preprocessing order remain bound.

The policy consumes only the frozen fit available at that timestamp. Independent per-factor
percentiles need not be the only inputs; preserve magnitude or use joint/conditional inputs
when the hypothesis requires them. Choose model complexity from train support and stability;
this interface does not require machine learning or force a particular algorithm.

Verify that a fitted combination replays identically after serialization, that later data
perturbations cannot change earlier fits/decisions, and that any permitted refit respects
both fit cutoffs and actual label availability. Include fitted-state identities in cache,
parent comparisons and final freeze, alongside factor fits and policy state-transition rules.

## Completion belongs to the worker

Use [trial_records.py](../scripts/trial_records.py) inside the numerical worker, not a later
agent-authored `collect` call. Both Environments use the same study receipt root, with unique
evaluation IDs and requests binding scope, candidate, data, plan, engine, costs and fitting.
The helper serializes short metadata writes with a POSIX lock; it does not hold a lock over
the numerical calculation or replace Manager worker admission/cancellation.

```python
records = TrialRecords(receipt_root)
records.plan(evaluation_id, request)  # planned only; no claimed data look
receipt = records.execute(evaluation_id, request, compute)
# compute() returns {"payload": result_object,
#                    "outputs": [{"path": artifact_path, "sha256": artifact_hash}]}
```

`execute` records dispatch, atomically saves the result and output hashes, then records
completion before returning. Errors remain errors; weak market performance is a computed
result. Identical completed requests reuse verified results; changed requests need new IDs.
For async actions call it inside the joined numerical worker; do not pass an unawaited
coroutine or detach a writer that survives action cancellation.

Use `observe(id)` when the result is actually exposed to the researcher, including returned
metric summaries used for decisions. Reading only progress/hash metadata is not a score look.
Repeated observations remain recorded; they are not independent samples. Record research
reviews separately with their result IDs; a computed or observed result is not automatically
reviewed, admitted or a new mechanism.

Use `reconcile()` to rebuild status/catalog/report inputs from verified receipts, without
requiring a completed round summary. After Manager/job has stopped and joined workers, use
`reconcile(workers_stopped=True)` to distinguish interrupted dispatches, still-planned work
and completed results awaiting review. Invalid/tampered output hashes cannot count as
computed evidence. Report snapshot date/round separately from current run status. Old reports
remain immutable; a stopped run cannot inherit an old snapshot's “research continues” status.

## One frozen final operation

Before final access, verify readiness against the agent's prospective evidence standard.
Freeze candidate/spec/implementation hashes, factor and combination fits or causal refit
policy, plan/scenarios, engine, costs, benchmark and source query contract in one bundle.
Both Environments use `records.reserve_final(bundle, prior_exposure=...)` on one shared root
BEFORE retrieving test. It atomically persists the full bundle and history classification;
another bundle cannot claim the same final access. An interrupted identical bundle may resume.
Use `records.bind_final_snapshot(bundle, downloaded_path)` before scoring and on replay;
it binds and verifies the actual downloaded file hash without allowing replacement.

Ordinary research actions remain train-only. A separate final action validates the reserved
bundle and returns the cached final result when available, using the existing accounting
engine on the frozen test schedule. Test-time rolling refits are allowed only if their
past-only schedule was frozen; neither the agent nor the numerical optimizer may select
models or parameters from revealed test scores. Factor diagnostics and predeclared controls
belong to that same final operation, never a separate early test-ranking endpoint.

`prior_exposure` is `previously_exposed`, `no_known_exposure` or `unknown`, taken from the
study's disclosure. It is separate from “this run has not accessed test yet.” The current
Signal Foundry historical test was previously exposed, so final results are historical
diagnostics, not new independent confirmation. This helper is an auditable protocol under
the demo's editable environment; externally enforced isolation requires a different trust
boundary. Data must not be fetched by status/report generation.
