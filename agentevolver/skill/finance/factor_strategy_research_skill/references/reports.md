# One continuous factor and strategy report

Read [metrics-and-evaluation.md](metrics-and-evaluation.md) before exporting numerical results.
The Agent analyzes JSON; HTML, local CSS and JS provide the human visualization at the
deployment URL. Reuse the bundled renderer and canonical visual theme. Browser operation
and a separate frontend implementation workflow are not required for this research demo.

Sections: [page contract](#page-contract), [information hierarchy](#visible-information-hierarchy),
[chart specifications](#chart-specifications), [result artifacts](#shared-result-artifacts-and-agent-analysis),
[report adapter and schema](#report-adapter), [visual delivery](#visual-design-and-delivery),
[artifact acceptance](#artifact-and-numerical-acceptance).

## Page contract

Deliver **one continuous document at one report URL**. Overview, Factor Observatory,
Strategy Atelier, research history and definitions all occupy the normal page flow and
remain present together. Readers can inspect the visualization by scrolling; the Agent reads its JSON artifacts. Do not put factors and strategies on separate routes,
tabs, page switches, modal-only views or collapsed sections. Train, validation and test
are labelled adjacent columns or consecutive sections, not mutually exclusive tabs.

Provide a compact sticky table of contents with in-page anchors. Links from a factor to a
consumer strategy scroll to its inline definition/evidence; they do not replace the page.
Sorting, optional local filters, chart brushing and an inline comparison inspector are
welcome. A local control must not hide the other research stage or change the official
scores. Show all real candidate summary rows by default;
do not require pagination, virtual scrolling or clicking every candidate to discover which
factors/strategies were evaluated and how they performed. Long trade ledgers may scroll in
a labelled table region with an explicit total, local filters and a complete permitted
export. Reset clears local investigation filters without replacing any report section.

Save both responsibilities in each round's versioned JSON results. Generate the continuous
page from a selected completed snapshot; unfinished sections remain explicitly pending.
Include completed screen results without requiring full qualification artifacts for every
row. Label depth, pending evidence and the snapshot's as-of time. Publish the useful bundled
view first; ready factor/strategy revisions do not wait for custom controls or UI extensions.
Reuse one adapter for the actual engine schema and update it only for changed fields or a
concrete defect. The eventual report still covers the required research results and charts.
Publish a useful milestone/final report without a mandatory two-stage release sequence.
Keep previous round/report versions intact while the main deployed URL shows the selected
latest result. Report iteration does not require a new browser session or deployment.
Publication is a progress milestone, not a research stopping rule. Show preliminary eligibility,
submission readiness and final acceptance separately. Keep the frozen attempt's failure visible
when research continues, alongside current exploratory activity and unsupported strategy claims.
If confirmation awaits new data, state that dependency; do not relabel later validation results
as a replacement passing test. Show resource usage, any actual limits and the next hypothesis or evidenced stop
reason. Follow the [completion decision](research-workflow.md#completion-decision) when deciding whether work ends.
Show its evidence and counterevidence by quality dimension, remaining investigations and the
Agent's reasoned continue/complete decision. Research completeness, strategy support and
resource interruption need separate labels; a large return or candidate count cannot decide them.

## Visible information hierarchy

| Section | Always-visible content |
| --- | --- |
| Study overview | Stock, source/coverage/quality, split timeline, prospective objectives/benchmarks/tradeoffs, as-of time, counts by execution/selection state, validation-selected strategy, final gates and next question. Separate engineering readiness from market results. |
| Factor inventory | Every proposed and executed factor with ID/version, readable formula, rationale, lookback, availability, direction/horizon, train and per-fold validation IC/RankIC, primary mean RankIC, coverage, non-overlapping count, redundancy and admission/rejection reason. Mark unexecuted rows pending, with no invented performance. |
| Factor evidence | Inline fitted-definition details and the predictive/coverage/stability charts below; selected-factor diagnostics after the joint final test only. Connect admitted versions to the strategies that use them. |
| Strategy inventory | Every candidate/version, exact factor composition, readable entry/exit/sizing/rebalance/risk rules, train and validation performance, costs, completed trips, failed gates and selection reason. Readers can answer what it actually trades without reading source code. |
| Strategy evidence | Selected strategy and baseline curves, aligned split/fold metrics, costs, exposure, drawdown episodes, trades, parameter comparisons and factor ablations. Final test shows only the frozen strategy and predeclared scenarios. |
| Research decisions | Chronological hypothesis → experiment → baseline/candidate metric changes → diagnosis → decision → next experiment; include rejected, errored and blocked trials, budgets and validation usage. |
| Research routes | Information and combination/holding mechanisms, linked versions, behavioral similarities, each shortlist diagnosis, measured factor-only/policy-only/joint changes, park/reject reasons and next question. Compare prospective claims separately in the same document. |
| Definitions and evidence | Plain-language metric definitions, units, denominators, scope, uncertainty, execution assumptions, source rights, result identities, limitations and permitted JSON/CSV exports. |

Use a compact comparison table for all candidates and readable inline detail for selected
comparisons; long expressions may wrap.

For the selected route, put its benchmark definition, average exposure, active-session count,
main counterevidence and confirmation status in the overview. Where the study requests both
calendar-account and fold-reset results, show them side by side with separate scope names,
actual date ranges, reset/exclusion rules and annualization denominators. Label a daily
constant-weight benchmark as rebalanced rather than initial-allocation buy-and-hold.
Show a pending required calendar comparison explicitly; never fill its gaps or calculate
missing market metrics in the renderer. Confidence captions state whether the interval
conditions on a selected fixed candidate or accounts for a broader fitting/search procedure.
Show exact parent/version links and strategy-specific factor roles. Use the metric contract's
role-specific evidence for supporting factors; directional IC charts apply to predictive roles.
Explain role-inapplicable metrics instead of filling supporting-factor rows with unavailable
predictive statistics. Show factor-role evidence, exact consumer contribution and complete
strategy eligibility separately; a failed consumer must not erase promising factor evidence.
Preserve return/risk/cost tradeoffs and parent deltas, including missed upside. Distinguish
new mechanisms from revisions, parameter controls and invalid definitions in the counts.
Use compact comparison tables rather than replacing definitions/results with only counts,
ranking badges or generic "signal quality" scores. Before test reveal, its section says
sealed/pending and explains prerequisites; no test metrics or bars are shipped in HTML,
JavaScript, JSON, downloads or network responses. After reveal the official result is fixed.

## Chart specifications

Each chart has a question it answers, labelled axes/units, scope and result identity, exact
series names, sample count, baseline, aggregation and null/empty behavior. Use the frozen
metric contract for definitions. Include a concise text conclusion and an adjacent table
or permitted export of the underlying values so the agent can analyze evidence directly.
Display uncertainty method/support where applicable, not unexplained error bars.

| Chart and question | Axes and series | Required conventions |
| --- | --- | --- |
| Factor fold bars: is predictiveness stable? | x = candidate (grouped by validation fold); y = oriented primary RankIC, unitless, with each fold and the equal-fold mean identified. | Zero baseline; positive and negative bars; explicit horizon/direction and counts. The frozen admission threshold applies to the mean, not every bar. An undefined fold is a labelled gap, not zero. |
| Factor horizon lines: how quickly does the signal decay? | x = label horizon in trading sessions; y = oriented IC/RankIC; distinguish statistic and fold with labelled series or separate aligned plots. | Fixed research horizon grid and zero baseline; mark the training-selected primary horizon. Do not select a new horizon from the final-test chart. |
| Rolling RankIC lines: when is the factor unstable? | x = latest included label's exit date; y = trailing oriented RankIC for selected factors. | Display window/minimum support and fold boundaries; break at insufficient data/gaps. Explain that the statistic becomes known at label exit, not signal time. |
| Quantile response bars: are higher scores followed by higher returns? | x = training-fitted low-to-high factor bins; y = mean gross h-session forward return (%); counts and dependence-aware intervals in tooltips/table. | Zero baseline; medians and top-minus-bottom difference alongside. Empty/tied bins visible. This is conditional label response, not a compounded tradable long-short curve. |
| Coverage bars and redundancy matrix: is evidence usable/complementary? | x = factor/fold, y = available/paired/eligible counts or explicitly labelled coverage %; matrix axes = exact factor versions, cells = Spearman factor correlation. | Show missingness and denominator; label signed coefficients and the absolute cutoff. Pair counts accompany correlations; undefined cells remain undefined. |
| Equity lines: does the strategy add value after costs? | x = actual scored sessions; y = growth of common starting capital (default indexed to 100); lines = net strategy, zero-cost replay, matched net buy-and-hold and cash. | Separate aligned train, validation and test plots with the same series colors and labelled scales. Show initial value, costs and terminal liquidation; never concatenate all splits into a claimed unseen curve. Label validation fold resets/composite gaps. |
| Drawdown lines/areas: how deep and long are losses? | x = same sessions as equity; y = drawdown %, at or below zero, for net strategy and benchmark. | Include initial capital in peaks; aligned time axes and peak/trough/recovery markers; optional shared brush. Gate/table maximum drawdown uses positive loss magnitude. Mark ongoing episodes unrecovered. |
| Return bars: which periods contribute? | x = calendar month/year, y = compounded net period return %, grouped strategy/benchmark. | Zero baseline, partial-period and split/fold labels; never sum daily returns. Optional heatmap supplements rather than replaces the readable period table. |
| Exposure and cost attribution: what caused the drag? | x = session for actual/target exposure (%); separate period bars for commission/slippage (currency or explicitly labelled bps of initial capital). | Do not overlay currency and percentages on an unlabelled axis. Show actual weights versus prior-close targets, turnover definition and fill count; gross/net replay difference is not automatically sum of fees. |
| Cost stress bars: does it survive the declared scenarios? | x = frozen cost scenario; y = net total return %; adjacent labelled table for net Sharpe, drawdown and costs. | Default/stress scenario boundaries and zero baseline; rerun the frozen policy without tuning. No sliders that launch new test experiments. |
| Ablation/parameter bars: what actually helps? | x = named research variant; y = delta in one specified validation metric versus the full/fixed baseline; separate plots for return percentage points, Sharpe and drawdown. | Same scored dates/cost/refit scope; show every tested variant, paired uncertainty if valid and trial IDs. An omitted factor's whole-strategy delta is not per-trade causal PnL attribution. |
| Route refinement bars: did the factor or strategy revision help? | x = named parent/candidate comparison, y = candidate-minus-parent delta in one metric with its own units. Separate factor role utility, net strategy performance and risk changes. | Show parent and candidate versions/values, exact bindings, matched scopes and regression explanations. Lower drawdown is a negative raw delta. Never aggregate incompatible units into a generic improvement score. |
| Behavioral diversity matrix: are policies doing different things? | Rows/columns = strategy versions; separate cells for return correlation, target-exposure correlation and active-session overlap. | Use the metric contract's aligned samples, definitions and null states. Pair with mechanisms and fold/regime behavior; small correlation alone is not proof of discovery. |
| Trade distribution bars: is success concentrated? | x = frozen net episode-PnL or holding-session bins; y = completed round-trip count, with zero-PnL boundary and sample size. | Explain bin edges, completed versus open inventory, median/tails and wins/losses. Adds/resizes do not create extra trips; link bars to rows in the trade ledger. |

Keep official train/validation/test summary columns visible simultaneously; local brushing
can show a separate "selected interval — descriptive" summary, never overwrite official
gates. Compare like with like: same dates, price basis, horizon and cost scenario. Unavailable
benchmark/metric evidence is labelled, not omitted to improve apparent relative performance.
No smoothing of returns or fitted trend lines that obscure the measured series. If dense
series need display downsampling, disclose it and retain exact extrema, gate calculations
and the full permitted data export; tables/metrics remain based on the complete series.

## Shared result artifacts and agent analysis

Generate one versioned report manifest/dataset from the environments' saved result artifacts.
Field names can follow the implemented schema, but it must contain:

- Study/protocol/metric-contract identities, source snapshot, actual coverage, as-of time,
  split/fold definitions and holdout state.
- All candidate definitions, execution/selection states, parent IDs and consumer links.
- Route/family identities, scoped factor roles, every shortlist review, and paired revision
  results. Use schema 3 of the report adapter so these records survive into analysis.json
  and the same continuous page, rather than existing only in an unlinked research notebook.
- Per-result metric values/status/reasons, counts, criterion records and uncertainty method.
- Chart series with explicit timestamps/bins, units, metric IDs, scope/scenario and result IDs;
  references to full equity, order, trade and factor-diagnostic artifacts.
- Trial/decision history and comparisons linking baseline and candidate result identities.

Use relative downloadable artifact URLs, not host paths or credentials. Provide both a
compact analysis JSON (candidate metrics, gates and next-decision evidence) and complete
permitted CSV/JSON details. The Agent reads these files directly for numerical comparison. It does not need to read
HTML, execute page scripts or operate a browser. The HTML shell may fetch analysis.json;
all scoring and selection evidence must already exist in the saved JSON.
Link detailed research judgments and artifact paths from the plan index rather than
copying large arrays/reports into prompt context.

Reconcile chart points, tables, ledgers, gates and exports from the same result versions.
Never hand-author market performance values. Include a visible build/result identity;
update manifest and assets atomically so a refresh cannot mix old metrics with a new curve.
Distinguish old immutable releases from current results. A fixture demonstration stays in
engineering evidence and cannot populate real factor/strategy tables or final gates.

### Round records and direct analysis

Follow the workflow's [directory/version conventions](research-workflow.md#directory-and-version-conventions).
Use catalog.json for discovery, rounds/<round>/results.json for candidate/evaluation bindings
and rounds/<round>/pool.json for selection, diagnoses and next hypotheses. Exact engine JSON
records own numerical values; analysis.json is a source-bound visualization export, not a
second calculator. Read selected candidate metrics and decisions first, detailed series only
when needed. Never dump every historical report or all curves into prompt context.

The report manifest may include `record` with nonempty `round_id`, `report_id`, `phase` and
optional `parent_report_id`. This identity survives into analysis.json and the visible report.
Keep exact evaluation IDs/paths/hashes in the private round catalog and permitted source
hashes in the public report. An immutable round snapshot binds results and pool decisions;
a catalog pointer can advance without rewriting older records. New numerical evidence
gets a new evaluation; presentation-only corrections get a new report version.

Render to e.g. rounds/R001/reports/v001. Repeating identical output is idempotent; different
content at the same output directory is refused. Use v002 for a changed report, then update
the catalog and optional live deployment after output succeeds. The HTML shell, JS, JSON
and exports are one versioned package. Preserve source JSON files alongside private numerical
records so public source hashes can be traced to actual results.

## Report adapter

The adapter verifies the first real numerical-to-report integration: nonempty metrics and
measured chart series. Complete [local data acceptance](data-and-environments.md#connector-contract)
first, bind both engines to the verified snapshot, and export their actual results as JSON.
Reference those saved values in the manifest; never type market results by hand. Sources
carry `scope: research` or `scope: synthetic`; mixing scopes is rejected. Environments own
calculations. The adapter is neither a backtester nor a statistical judge.

### Commands

```bash
python {skill_dir}/scripts/report.py check /absolute/report-manifest.json --stage factors
python {skill_dir}/scripts/report.py render /absolute/report-manifest.json --stage factors --output /absolute/workspace/research/rounds/R001/reports/v001
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
Render to a new version directory, run artifact acceptance, then publish that directory using deploy_tool.

### Report data contract

Root fields:

| Field | Contract |
| --- | --- |
| schema, study_id, title, summary | Schema 3 for new research with archived strategy definitions. Schemas 1/2 remain readable for existing artifacts. |
| record | Optional round_id, report_id, phase and parent_report_id identifying a saved round/report version; included in analysis.json and visualization. |
| scope, data_basis | research/synthetic; explicit source, price/volume basis, costs and limitations. |
| strict_data | status met/unmet and a list of reasons; public research can proceed with unmet strict qualification when authorized. |
| test_state | sealed/evaluated. The latter requires the real frozen finalization receipt; setting this field does not authorize access. |
| sources | Map of stable result IDs to path and SHA-256. Paths resolve relative to this manifest; each result JSON has the same scope as the manifest. |
| factors, strategies | Arrays of all proposed and executed candidates, preserving rejections/errors. |
| charts | Measured line/bar series referencing source JSON; never fake coordinates or all-null arrays. |
| routes, comparisons | Schemas 2/3 research routes and measured parent/candidate comparisons, described below. |

Each candidate needs unique `id`, `name`, `status` and its exact `formula` (factor) or
`rules` (strategy). Include `reason` for a diagnosis/decision and `factor_ids` for a strategy.
Statuses are proposed, blocked, error, evaluated, admitted, rejected; they are the adapter's
display state, not replacements for separate execution/selection state in the research ledger.
In legacy definitions, `baseline: true` permits an empty factor binding list. New definitions
derive the flag from their explicit research role as described below. Controls still need
exact rules and computed metrics; they are not mined strategies.
Only executed candidates carry measured metrics. An evaluated training factor can support
an exploratory training strategy, but validation eligibility still requires actual admission.
A failed factor cannot be silently consumed as an admitted factor.

For staged reports, put the snapshot's as-of time in `summary`, its stage in `record.phase`,
and each row's evaluation depth/pending checks in `reason`. Those fields survive compilation
and display. Keep structured depth and diagnostic receipts in the source evaluation JSON;
the adapter does not preserve arbitrary extra manifest fields. `--stage integrated` checks
numeric results from both sections, not full qualification. Use `evaluated` with
`research_only: true` for a screened strategy awaiting admission, and retain its route with
the next refinement in `next_step`; do not label it `admitted` merely to publish the snapshot.

### Joint research records (schemas 2/3)

Use schema 3 for new work so complete strategy definitions, lineage, roles and reviews
survive compilation, display and the analysis.json download. Schema 2 retains its earlier
metadata contract for existing artifacts. Candidate IDs identify exact versions. Each candidate adds
`family`, `hypothesis` and `parent_ids` (empty for an original); parents must exist in the
same factor/strategy inventory, with no cycles. Family labels are researcher-defined;
the adapter does not infer economic diversity from different strings.

In schema 3, each strategy supplies `strategy_spec: {source, pointer}` referencing the complete
[validated strategy definition](research-workflow.md#strategy-definition-archive) embedded in
an evaluation/source JSON already covered by `sources` and its hash. The exporter resolves
that definition and derives id, name, family, hypothesis, parents, factor IDs/roles and readable
rules. These need not be manually copied into the report manifest; if supplied, conflicting
values are rejected. The row still supplies status, metrics, reason and research_only as
appropriate. Pending proposals may reference an unimplemented definition; measured strategies
must have a pinned implementation record. File/entrypoint verification occurs in the engine,
not by opening implementation paths from a public report.

New strategy definitions use spec schema 2 and explicit research_role/control_for. The report
derives these roles from the hash-bound definition: formal candidates, ablations and benchmarks
have separate counts/labels. Routes and pool membership contain formal candidates, while
control_for links diagnostics to those candidates for comparisons. Controls cannot be admitted
as selected strategies. Keep both full and reduced policies in the inventory to make the
contribution evidence readable without inflating discoveries. Historical spec schema 1 stays
readable as legacy_unclassified; it does not silently become a formal candidate.

analysis.json and CLI receipts expose strategy_counts: candidate (version records),
candidate_hypotheses (distinct IDs), evaluated_candidate_hypotheses, ablation, benchmark and
legacy_unclassified. Proposed and evaluated coverage remain distinguishable; a new ID alone
does not prove a new economic mechanism. strategy_records is the total including controls.

analysis.json preserves the full `strategy_spec`, canonical `spec_sha256`, `definition_source`
reference, and directly queryable strategy_id, version, description and created_round. This
includes assumptions, failure conditions, fitting policy, pseudocode, parameters and change
motivation; the JS displays key design and version information. New-schema factor rows also
require explicit display names. Report checks validate structure and consistency, not whether
an economic explanation is correct. Private spec/evaluation paths stay in the catalog;
implementation file paths inside archived specs are relative to their version directory.

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
`factor_ids`, `strategy_ids`, `diagnosis` and `next_step`. All formal candidates and research
factors belong to at least one route; controls link through control_for, outside strategy_ids.
Early routes can have factors and no strategies. Keep the next step
or an explicit closure condition even for parked/rejected routes. Record role-specific
criteria, budgets and all historical decisions in the full plan records linked by the index.

Each measured comparison has `id`, `route_id`, `parent_id`, `candidate_id`, `diagnosis`,
`decision` and nonempty `metrics`. Both candidates are executed versions of the same kind;
the candidate belongs to that route or is an ablation of one of its strategies. Each metric supplies `label`, `definition`, `unit`,
`split` and `parent`/`candidate` references (`source`, `pointer`) to numerical artifacts.
Both references must identify exactly one exported candidate metric with the comparison's
label/split/unit and the same metric definition. Keep horizon, aggregation and evaluation
conventions explicit and identical for paired metrics; do not subtract Pearson IC from
RankIC or different horizons merely because they share a unit. The comparison definition
explains the paired question and does not replace either source metric's definition.
References must also appear in the respective candidate's metric list with matching split
and unit. The compiler resolves both finite values and exports `delta = candidate - parent`;
the UI does not recalculate performance. Same dates/folds/costs/fitting policy must be checked
in the environment. Null/unsupported comparisons remain pending in route diagnoses rather
than fake measured rows. Comparisons are displayed inline and exported to comparisons.csv.
Do not use comparison records to rerank post-reveal test candidates.

Source hashes and references apply to all comparison values; sealed test labels are rejected
in comparisons as in charts/metrics. Verify that a source does not disguise test data under
another label. Neither this structural adapter nor report schemas independently enforce holdout
isolation, mechanism coverage, trial budgets or statistical admission. Those are the native
environments' research contracts and the agent's evidence-based review responsibilities.

### Metric and chart references

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

## Visual design and delivery

Use the existing AgentEvolver visual theme, with the canonical CSS at
`{package_root}/visual/benchmark/style.css`: deep green background #07100e, green panels
#0e1c19, light text #ecf7f2, secondary text #8da69c, mint #63e6b5, amber #f3bd71 and red #ff7f87.
Copy the CSS into deployed assets so the page never depends on an inaccessible host path.
Use mint for emphasis/success, amber for pending/qualified and red for failures; series names,
line styles and signs must also distinguish states. Choose layout and typography within
this theme in the detailed plan. Use
a clear editorial hierarchy, readable chart labels, generous space around interpretation
and compact comparison tables. Keep factor/strategy identity and train/validation/test
semantics consistent throughout. Color is supplemented by names, line styles and signs.
Avoid decorative plots, tiny axes, giant empty cards and repeated tiles without analysis.

Reuse the supplied responsive renderer, keyboard controls, chart tooltips and inline
details. Keep both stages in one document, with local assets and readable tables. Modify
the renderer when a concrete presentation requirement or defect warrants it, then check
that change; building a new frontend validation framework is not a research deliverable.

Publish useful milestones and the final report via deploy_tool using its returned gateway
URL. Serve the shell, assets and matching JSON together. If acquisition fails, preserve
the specific missing prerequisite and any real progress; an elaborate synthetic dashboard
does not resolve missing research data.

## Artifact and numerical acceptance

Use Bash/file access and HTTP checks; no browser environment, frontend-testing skill or
screenshots are required. Numerical verification remains necessary. These checks establish
data integrity and delivery, not visual or interactive browser validation.

1. **Adapt real results.** Reuse a reader for the actual versioned engine schema. Reconcile
   IDs, counts, roles, bindings and statuses with the completed round. Run the adapter check;
   compare metrics, deltas and representative chart points to hash-bound outputs in the same
   scope and units. Preserve null reasons and zero values. Invalid/unexecuted results must
   remain explicit; an all-null series need not produce a chart. Check source scope as well
   as labels: sealed test data stays absent, and revealed failures remain visible.
2. **Freeze a report snapshot.** Finish the adapter in a staging directory, then archive the
   report ID with its inputs, source hashes and assets. Bind immutable results or catalog
   snapshots, never the live catalog/index. Mark resource totals with their observation time;
   subsequent cost changes do not require rebuilding the report. Keep report receipts and
   the live catalog outside the frozen source directory.
3. **Check local delivery.** Verify shell/JS references resolve to the matching local JSON
   and assets. Reuse the supplied renderer's valid checks. If renderer code changes, check
   JS syntax and the affected behavior on representative records; do not invent an exhaustive
   per-candidate DOM audit or require numeric data in the HTML shell.
4. **Preview and release.** Call deploy_tool action=preview first. Request the returned HTML,
   JS, CSS and JSON over HTTP, comparing served JSON with the saved snapshot. HTTP 200 for
   an empty shell alone is insufficient. Keep source files unchanged, then action=deploy;
   verify the release URL and matching snapshot. A changed source needs a new preview.
   If an unchanged runtime gate requires an unavailable browser, diagnose the configuration
   conflict; do not fabricate receipts, keep retrying or create report versions to evade it.
5. **Record and finish.** Save the delivery receipt and report ID/path in the index. Correct
   an archived report with a new version only when its content must change. Reuse valid
   numerical/renderer evidence and check only affected dependencies. Once delivery passes,
   return to the recorded completion decision instead of another presentation review.

Keep numerical correctness, delivery, unverified browser behavior and strategy support
separate. Report an unresolved release prerequisite honestly; a preview is not a release,
and a successful render cannot make a strategy pass.
