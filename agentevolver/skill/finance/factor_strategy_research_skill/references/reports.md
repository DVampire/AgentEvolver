# One continuous factor and strategy report

Read frontend_ui_engineering_skill and its planning reference before implementation, and
[metrics-and-evaluation.md](metrics-and-evaluation.md) before binding any numbers to the UI.
Task HTML is the product brief; the agent designs and implements the actual application.

## Page contract

Deliver **one continuous document at one report URL**. Overview, Factor Observatory,
Strategy Atelier, research history and definitions all occupy the normal page flow and
remain present together. Readers and the researching agent must be able to inspect the
whole investigation by scrolling. Do not put factors and strategies on separate routes,
tabs, page switches, modal-only views or collapsed sections. Train, validation and test
are labelled adjacent columns or consecutive sections, not mutually exclusive tabs.

Provide a compact sticky table of contents with in-page anchors. Links from a factor to a
consumer strategy scroll to its inline definition/evidence; they do not replace the page.
Sorting, optional local filters, chart brushing and an inline comparison inspector are
welcome. A local control must not hide the other research stage or change the official
scores. Show all real candidate summary rows by default (the study bounds their number);
do not require pagination, virtual scrolling or clicking every candidate to discover which
factors/strategies were evaluated and how they performed. Long trade ledgers may scroll in
a labelled table region with an explicit total, local filters and a complete permitted
export. Reset clears local investigation filters without replacing any report section.

Stage one publishes an early version of this same page: measured factors, strategy progress
and explicit pending areas. Stage two fills out and improves that page. Two research-bearing
releases mean two versions over time, not two report pages. Preserve older immutable release
links for audit, while the main product link opens the latest integrated document.

## Visible information hierarchy

| Section | Always-visible content |
| --- | --- |
| Study overview | Stock, source/coverage/quality, split timeline, as-of time, research status, candidate counts by execution/selection state, validation-selected strategy, all final gate states and the next research question. Separate engineering readiness from market results. |
| Factor inventory | Every proposed and executed factor with ID/version, readable formula, rationale, lookback, availability, direction/horizon, train and per-fold validation IC/RankIC, primary mean RankIC, coverage, non-overlapping count, redundancy and admission/rejection reason. Mark unexecuted rows pending, with no invented performance. |
| Factor evidence | Inline fitted-definition details and the predictive/coverage/stability charts below; selected-factor diagnostics after the joint final test only. Connect admitted versions to the strategies that use them. |
| Strategy inventory | Every candidate/version, exact factor composition, readable entry/exit/sizing/rebalance/risk rules, train and validation performance, costs, completed trips, failed gates and selection reason. Readers can answer what it actually trades without reading source code. |
| Strategy evidence | Selected strategy and baseline curves, aligned split/fold metrics, costs, exposure, drawdown episodes, trades, parameter comparisons and factor ablations. Final test shows only the frozen strategy and predeclared scenarios. |
| Research decisions | Chronological hypothesis → experiment → baseline/candidate metric changes → diagnosis → decision → next experiment; include rejected, errored and blocked trials, budgets and validation usage. |
| Definitions and evidence | Plain-language metric definitions, units, denominators, scope, uncertainty, execution assumptions, source rights, result identities, limitations and permitted JSON/CSV exports. |

Use a compact comparison table for all candidates and readable inline detail for selected
comparisons; long expressions may wrap. Do not replace definitions/results with only counts,
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
| Drawdown lines/areas: how deep and long are losses? | x = same sessions as equity; y = drawdown %, at or below zero, for net strategy and benchmark. | Include initial capital in peaks; shared time brush and peak/trough/recovery markers. Gate/table maximum drawdown uses positive loss magnitude. Mark ongoing episodes unrecovered. |
| Return bars: which periods contribute? | x = calendar month/year, y = compounded net period return %, grouped strategy/benchmark. | Zero baseline, partial-period and split/fold labels; never sum daily returns. Optional heatmap supplements rather than replaces the readable period table. |
| Exposure and cost attribution: what caused the drag? | x = session for actual/target exposure (%); separate period bars for commission/slippage (currency or explicitly labelled bps of initial capital). | Do not overlay currency and percentages on an unlabelled axis. Show actual weights versus prior-close targets, turnover definition and fill count; gross/net replay difference is not automatically sum of fees. |
| Cost stress bars: does it survive the declared scenarios? | x = frozen cost scenario; y = net total return %; adjacent labelled table for net Sharpe, drawdown and costs. | Default/stress scenario boundaries and zero baseline; rerun the frozen policy without tuning. No sliders that launch new test experiments. |
| Ablation/parameter bars: what actually helps? | x = named research variant; y = delta in one specified validation metric versus the full/fixed baseline; separate plots for return percentage points, Sharpe and drawdown. | Same scored dates/cost/refit scope; show every tested variant, paired uncertainty if valid and trial IDs. An omitted factor's whole-strategy delta is not per-trade causal PnL attribution. |
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
- Per-result metric values/status/reasons, counts, criterion records and uncertainty method.
- Chart series with explicit timestamps/bins, units, metric IDs, scope/scenario and result IDs;
  references to full equity, order, trade and factor-diagnostic artifacts.
- Trial/decision history and comparisons linking baseline and candidate result identities.

Use relative downloadable artifact URLs, not host paths or credentials. Provide both a
compact analysis JSON (candidate metrics, gates and next-decision evidence) and complete
permitted CSV/JSON details. The agent should read these for numerical comparison and use
the browser for rendered verification; screenshots are not the primary numeric data source.
Link detailed research judgments and artifact paths from the plan index rather than
copying large arrays/reports into prompt context.

Reconcile chart points, tables, ledgers, gates and exports from the same result versions.
Never hand-author market performance values. Include a visible build/result identity;
update manifest and assets atomically so a refresh cannot mix old metrics with a new curve.
Distinguish old immutable releases from current results. A fixture demonstration stays in
engineering evidence and cannot populate real factor/strategy tables or final gates.

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

Use [the report adapter](report-data.md) for the first successful numerical-to-visual path.
Run its check before styling or publishing; all-null candidate metrics or missing measured
series must fail that milestone. Its renderer is an extensible starting point with actual
SVG charts, tables and downloads. Complete the detailed chart/metric acceptance above;
passing the structural checker alone does not certify statistical correctness or completeness.

The page must work with keyboard focus, anchor links, chart tooltips, reset controls and
responsive layouts. Tables may scroll horizontally within a labelled region on narrow
screens; headings, conclusions and primary controls must not clip. Printable/exportable
HTML contains both stages and definitions without visiting hidden tabs. Preserve already
visible findings while loading secondary artifacts; show useful retry/error states.

Publish via deploy_tool and use the returned gateway URL. First publish measured factor
research on the continuous report; later publish the integrated strategy evidence as a new
version of the same product. If data access fails before research begins, preserve a concise
status/evidence view and the specific missing prerequisite, with research acceptance unmet.
Do useful engine work; do not spend the remaining budget decorating a synthetic substitute.

## Browser and numerical acceptance

Use webapp_testing_skill and browser_environment on the deployed URL; HTTP 200 alone is
insufficient. Read the compact exported analysis before each research decision, then check:

1. Load the page once. Locate both factor and strategy inventories, definitions, results and
   all split headings in the same document. Scroll/anchor from a factor to its consumer and
   back; verify neither route navigation nor a tab/page replacement occurs.
2. Match candidate counts/states and exact formulas to saved results. Inspect an admitted
   and a rejected factor, when present, and trace an exact version into its strategy rules.
   If either state is absent, show that honestly rather than inventing an example.
3. Compare fold bars, horizon lines and quantile counts with factor artifacts; explain one
   admission/rejection from the actual gate values and uncertainty.
4. Brush a validation drawdown and inspect its fills, exposure and costs. Reset; official
   full-scope metrics/gates must remain unchanged. Reconcile one daily equity return, a
   drawdown, a cost total and a completed-trip count with the same result's ledger/export.
5. Compare only precomputed cost scenarios, read one ablation and its decision rationale.
   Verify metric units and train/validation/test identities in tooltip, table and export.
6. Confirm test is absent from payloads before reveal, including downloadable artifacts;
   afterward, all supplied final gates remain visible, including failures/nulls.
7. Repeat scrolling, anchor navigation and controls at desktop/narrow widths with keyboard.
   Inspect screenshots from overview, factor and strategy sections, not just the hero.

Record a concrete presentation defect, implement a purposeful improvement and compare
equivalent before/after views. Record this as self-review, not independent user approval.
Keep numerical correctness, usability, engine readiness and research success as separate
acceptance outcomes. Passing browser checks cannot turn a failed final test into success.
