# Research planning and durable records

The framework provides `index.md` and `plan.md` through the shared plan module. Use its
supplied paths. The task HTML states the product; the study specification states the
research constraints. Retain both staged input paths in the index so they survive compaction.
The Agent designs the implementation; the launcher does not generate the detailed plan.

Keep the layers separate: task.html describes the research product, while study.json
contains market assumptions and numerical research criteria. Framework component choices,
plan storage, capability adoption and deployment methods belong to the prompt and skills.
The demo configuration supplies runtime evidence requirements through the input manifest.
Read these alongside the task without copying them back into the product brief. Declare
the actual holdout boundary in the research records; it is an implementation fact, not a
property supplied by a stock's study specification.

Keep the index short: current stage/status, last verified result, next experiment, remaining
budget, holdout state and links to the authoritative records. Full plan text, reports, price
arrays and experiment ledgers do not belong in the live summary. Update after a meaningful
implementation, evaluation, decision, stage transition or blocker; reconcile with disk on resume.

The detailed plan should cover:

- The first executable milestone: native Connector download, saved-file path/hash and verified
  local OHLCV/calendar coverage. Data source feasibility, adjustment/action/volume semantics,
  research readiness and strict qualification are separate recorded checks. Do not begin
  market trials or report polishing with no accepted local dataset.
- Proposed connector and two environment interfaces, shared utilities and deterministic checks.
- Frozen chronological protocol, trial accounting, metric definitions/units/nulls/aggregation,
  admission rules and stop criteria from [metrics-and-evaluation.md](metrics-and-evaluation.md).
- Factor hypotheses, expected economic mechanism, strategy designs and falsification conditions.
- One continuous report page with factor and strategy inventories/evidence, chart specifications,
  shared result artifacts, visual direction, scroll/anchor journeys and browser checks.
- Implementation progress and acceptance evidence, including partial and failed outcomes.
- Capability baselines, candidates, version-scoped decisions and subsequent real consumer calls.

Track source access, engine implementation, engineering verification, real-data research and
report delivery separately. For an engine, name which successful operations run and which
remain missing; an adopted version is not automatically research-ready. On an access blocker,
the index names the external prerequisite and any independent work still worth doing. Pending
hypotheses stay distinct from executed research trials. On resume, read these records before
repeating probes or assuming that credentials alone unblock the generated implementation.

Choose extra files based on the work. A useful starting layout under the same plan directory:

```text
index.md
plan.md
research/contract.json            # immutable protocol and source/data identities
research/metric-contract.json     # versioned definitions shared by engines and report
research/trials.jsonl             # every attempt, parameters, exposure and cost
research/submission.json          # frozen factor/strategy/engine hashes
research/test-access.jsonl        # test requests, exposure and result identity
design/data-and-engines.md
design/report-experience.md
reviews/factors.md
reviews/strategies.md
reviews/research-decisions.md      # evidence, baseline/candidate changes and next hypotheses
reviews/visual.md
evaluations/                      # capability evaluation reports and receipts
```

This is guidance, not a framework-mandated directory schema. Machine-generated large
results, price snapshots, source and website assets belong in the workspace. Link their
absolute paths and hashes from the records. Export a small reproducibility manifest with
relative downloadable artifact links for report readers; do not expose internal host paths
or credentials in a public page.

Use stable candidate, dataset, fold, engine and result IDs across both stages. Record all
parameter trials, including crashes and rejected results, before evaluation begins. A
resumed run uses the same remaining validation budget and holdout-exposure ledger. Before
claiming completion, reconcile each acceptance criterion with its result ID and unresolved
items. A missing report or unexecuted check is pending, not implicitly passed.

Each research decision names the actual result IDs, metric values and gate failures that
motivated it, one bounded next hypothesis and its falsification condition. After execution,
record whether that change helped and why it was retained/rejected. Link the compact analysis
JSON and full artifacts from the index; do not replace these records with screenshots or
repeatedly place full factor/strategy tables in live context. Report versions update the same
single-page product; publication count and the number of pages are separate concepts.
