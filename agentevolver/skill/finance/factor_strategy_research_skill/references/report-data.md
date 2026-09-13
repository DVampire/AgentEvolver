# Saved results to a working report

This adapter checks the first real numerical-to-report integration, including nonempty
metrics and chart series. It is not a backtester, a source downloader, a statistical judge
or a substitute for the full [report contract](reports.md). Environments own calculations.

## Start with local data

Follow [data acquisition](data-and-environments.md) first. A native Connector response must
be saved locally and its hash, OHLCV and calendar checks must pass before real evaluations.
Bind both engines to that snapshot and export their numerical results as JSON. Save an
adapter manifest whose values reference those artifacts; do not type market results by hand.
Sources contain `scope: research` or `scope: synthetic`. Mixing scopes is rejected.

## Commands

```bash
python {skill_dir}/scripts/report.py check /absolute/report-manifest.json --stage factors
python {skill_dir}/scripts/report.py render /absolute/report-manifest.json --stage factors --output /absolute/workspace/public
python {skill_dir}/scripts/report.py check /absolute/report-manifest.json --stage integrated
```

Use `--allow-synthetic` only for isolated engineering checks, which display a synthetic
banner. Do not deploy these as a market-research release. For real inputs, a nonzero exit
means the integration is unfinished: repair the artifact/export/adapter or numerical code,
then repeat. Null metrics need reasons; every evaluated candidate needs a finite metric.
Every completed stage needs a real line/bar chart. Zero is valid and distinct from null.
The checker detects integrity/schema failures, not dishonest source labels or financial
calculation bugs; verify those separately with the numerical acceptance method.

The renderer writes a continuous HTML page, local CSS/JS, `analysis.json`, `metrics.csv`
and `series.csv`/`comparisons.csv`. It copies the installed package's canonical visual stylesheet. Both stages
remain in normal flow; anchors scroll without changing routes. SVG charts and visible tables
use the same saved values. Extend this layout for the full inventory, gates, uncertainty,
drawdown, costs and research-history requirements, preserving source-bound calculations.
Do not stop at the minimal charts just because the integration check passes. A custom UI
should consume the validated data rather than implement a second financial calculator.
Render to a staging directory, run browser acceptance, then deploy atomically using deploy_tool.

## Manifest contract

Root fields:

| Field | Contract |
| --- | --- |
| schema, study_id, title, summary | Schema 2 for joint research, exact study identity and truthful research summary. Schema 1 remains readable for legacy artifacts. |
| scope, data_basis | research/synthetic; explicit source, price/volume basis, costs and limitations. |
| strict_data | status met/unmet and a list of reasons; public research can proceed with unmet strict qualification when authorized. |
| test_state | sealed/evaluated. The latter requires the real frozen finalization receipt; setting this field does not authorize access. |
| sources | Map of stable result IDs to path and SHA-256. Paths resolve relative to this manifest; each result JSON has the same scope as the manifest. |
| factors, strategies | Arrays of all proposed and executed candidates, preserving rejections/errors. |
| charts | Measured line/bar series referencing source JSON; never fake coordinates or all-null arrays. |
| routes, comparisons | Schema 2 research routes and measured parent/candidate comparisons, described below. |

Each candidate needs unique `id`, `name`, `status` and its exact `formula` (factor) or
`rules` (strategy). Include `reason` for a diagnosis/decision and `factor_ids` for a strategy.
Statuses are proposed, blocked, error, evaluated, admitted, rejected; they are the adapter's
display state, not replacements for separate execution/selection state in the research ledger.
An explicitly labelled `baseline: true` strategy (for example cash/buy-and-hold) may have no
factor bindings; it still needs exact rules and computed metrics. It is not a mined strategy.
Only executed candidates carry measured metrics. An evaluated training factor can support
an exploratory training strategy, but validation eligibility still requires actual admission.
A failed factor cannot be silently consumed as an admitted factor.

## Joint research records (schema 2)

Use schema 2 for new work so lineage, roles and reviews survive compilation, display and
the analysis.json download. Candidate IDs identify exact versions. Each candidate adds
`family`, `hypothesis` and `parent_ids` (empty for an original); parents must exist in the
same factor/strategy inventory, with no cycles. Family labels are researcher-defined;
the adapter does not infer economic diversity from different strings.

Factors add `role`, using `return_prediction` for that role and open names for supporting
uses. `qualified_strategy_ids` restricts qualification to exact consumers. An admitted
supporting factor needs a nonempty list and each consumer must actually bind the factor.
For return-prediction factors an empty list allows general qualification within the frozen
policy; actual combination redundancy still needs evaluation. Strategies add `factor_roles`,
a mapping with exactly the same keys as `factor_ids`, matching those versions' roles.

`research_only: true` is required for evaluated strategies using factors not yet qualified
for that consumer, including diagnostic use of rejected factors. Such a strategy cannot
be admitted. An admitted strategy requires all
bindings to be admitted and covered by their qualification scope. Admission status here is
a reported research judgment; the engine must supply the actual role/consumer gate receipts.
The adapter checks consistency, not whether an economic claim or gate calculation is valid.

Each route has `id`, `hypothesis`, `status` (proposed/active/retained/parked/rejected/closed),
`factor_ids`, `strategy_ids`, `diagnosis` and `next_step`. All non-baseline candidates belong
to at least one route. Early routes can have factors and no strategies. Keep the next step
or an explicit closure condition even for parked/rejected routes. Record role-specific
criteria, budgets and all historical decisions in the full plan records linked by the index.

Each measured comparison has `id`, `route_id`, `parent_id`, `candidate_id`, `diagnosis`,
`decision` and nonempty `metrics`. Both candidates are executed versions of the same kind;
the candidate belongs to that route. Each metric supplies `label`, `definition`, `unit`,
`split` and `parent`/`candidate` references (`source`, `pointer`) to numerical artifacts.
References must also appear in the respective candidate's metric list with matching split
and unit. The compiler resolves both finite values and exports `delta = candidate - parent`;
the UI does not recalculate performance. Same dates/folds/costs/fitting policy must be checked
in the environment. Null/unsupported comparisons remain pending in route diagnoses rather
than fake measured rows. Comparisons are displayed inline and exported to comparisons.csv.
Do not use comparison records to rerank post-reveal test candidates.

Source hashes and references apply to all comparison values; sealed test labels are rejected
in comparisons as in charts/metrics. Verify that a source does not disguise test data under
another label. Neither this structural adapter nor schema 2 independently enforces holdout
isolation, mechanism coverage, trial budgets or statistical admission. Those are the native
environments' research contracts and the agent's evidence-based review responsibilities.

## Metric and chart references

Every metric gives `label`, `definition` (formula, denominator, horizon/fold/scenario),
`unit`, `split` (train/validation/test), `source`, `pointer` (JSON pointer), and a `reason`
if the resolved value is null. Unit `percent` stores a fraction (0.12 displays as 12%).
Use ratio, USD, sessions or observations for other units. A test-labelled field/chart is
rejected while sealed. Ensure sources/pointers themselves never smuggle test under train.

Each chart gives unique `id`, `title`, `kind` (line/bar), `section` (factors/strategies),
`split`, `x_label`, `y_label` and `series`. Each series gives `label`, `source`, `pointer`
to an array of rows, then `x` and `y` JSON pointers within each row. Line timestamps must
be sorted and unique; y values are finite numbers or null gaps. A line needs at least two
measured points, a bar at least one. Break paths at gaps; bars use a visible zero baseline.
Store fractional units consistently in source arrays and label chart axes accordingly.

For example, an engine result might contain `scope`, `rank_ic` and `rolling` with date/value
rows. The adapter references these, rather than copying their values:

```json
{
  "id": "F01@1", "name": "Twenty-session momentum",
  "family": "trend persistence", "parent_ids": [], "role": "return_prediction",
  "hypothesis": "Test whether past trend predicts the declared forward return",
  "formula": "close / delay(close, 20) - 1",
  "status": "evaluated", "reason": "Training diagnostic; admission pending",
  "metrics": [{
    "label": "RankIC", "definition": "Spearman correlation with next-open five-session return; training pairs",
    "unit": "ratio", "split": "train", "source": "factor-result",
    "pointer": "/rank_ic/value"
  }]
}
```

Bind `factor-result` to an actual exported path and its computed SHA-256 in `sources`.
Add chart references to its actual series. This fragment is a schema example, not evidence
of an evaluated factor. Hashes, values, dates and research judgments come from real results.
