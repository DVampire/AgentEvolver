# Factor and strategy report experience

Read frontend_ui_engineering_skill and its planning reference before implementation.
Use one coherent report website with two distinct research surfaces, shared source/split
controls and clear links between a factor, its admission evidence and a strategy using it.
Task HTML is a brief; the agent designs the actual application and iteratively improves it.

## Information and interaction

| Surface | Required content | A visitor should be able to do |
| --- | --- | --- |
| Study overview | Data provenance and quality, split timeline, progress, holdout status, frozen objective, engineering and research outcomes | Inspect what was measured and follow an unmet gate to its evidence |
| Factor Observatory | Hypotheses/formulas/versions, IC and RankIC, coverage, horizons, rolling stability, quantile responses, redundancy, admission/rejection | Sort/filter candidates, compare folds and factors, inspect a rejection and open a consumer strategy |
| Strategy Atelier | Factor composition, gross/net/benchmark equity, drawdown, returns heatmap, costs, exposure, trades, risk metrics, ablations | Compare research candidates, brush a drawdown interval and inspect its trades and factor contributions |
| Research history | Every trial, parent hypothesis, parameters, engine/data hashes, validation usage, cost and decision | Trace a hypothesis through rejected attempts to the frozen result |
| Reproducibility | Metric definitions, split boundaries, assumptions, source rights, engine checks, artifacts and known limitations | Download permitted results and reproduce the displayed numbers |

Separate train, validation and test explicitly. Before joint final evaluation, final-test
panels are unavailable and no test values are shipped to the browser, even in hidden JSON.
After reveal, the accepted result is read-only. A visitor changing a descriptive time range
does not create a new official score. Cost controls only switch among clearly labelled,
precomputed frozen scenarios; they must not submit new test optimization jobs.

Show uncertainty and null values with explanations. Display pending, blocked, failed,
inconclusive and passed honestly. A report can be complete while the strategy failed.
Separate engine engineering checks, financial objective and capability adoption; a green
registration badge must not imply profitable or independently verified research.

## Visual direction

Choose a specific typography, layout and palette in the detailed plan. Use a strong
information hierarchy and restrained accent colors, generous space around important
charts and denser tables for comparison. Align chart typography, axes, number formatting,
legend colors and drawdown conventions across both pages. Give loss, risk and uncertainty
distinct semantics; color alone must not carry meaning. Avoid decorative plots, tiny axes,
giant blank cards and repeated KPI tiles without investigative value.

Provide linked chart hover/brush interactions, readable tooltips, table sorting/filtering,
keyboard navigation, visible focus, reset controls, responsive navigation and useful empty
states. Tables may scroll on narrow screens; headlines and primary controls must not clip.
Preserve selected factor/fold in shareable navigation where feasible. Keep raw host paths,
credentials and internal registration schemas out of ordinary report UX.

## Data and delivery

Render from versioned machine-readable results, never numbers transcribed by the model.
Every chart/table carries a result ID, split, metric schema and data/engine identity. Reconcile
net/gross curves, trade costs, positions and aggregate metrics. Export tables must agree with
displayed values and precision. Preserve structured failures and count all attempted trials.
Separate distributable report artifacts from raw licensed source data.

Use deploy_tool and the existing deployment module. Publish a stage-one report preview and
a later integrated stage-two release on the monitoring gateway. An unfinished/test-sealed
preview is labelled as such. Use the returned URL; HTTP 200 alone is not a browser check.
These releases follow research progress. If source feasibility fails before research begins,
preserve a concise status/evidence view and any existing work; defer result-dependent surfaces
and mark their acceptance unmet. A synthetic accounting demonstration is optional engineering
evidence, not a replacement for the requested factor and strategy reports.

Verify concrete journeys with webapp_testing_skill and browser_environment:

1. Open overview, inspect split/data quality, enter factors and filter a rejected hypothesis.
2. Compare eligible factors, open one formula and trace its exact version into a strategy.
3. Select a validation fold, brush a drawdown, inspect trade rows and reset the selection.
4. Switch precomputed cost assumptions and reconcile a metric with the downloaded artifact.
5. Verify test is unavailable before reveal and a failed final gate remains visible afterward.
6. Repeat primary navigation and inspection at desktop and narrow widths, using keyboard too.

Capture and actually inspect rendered views. Record a concrete defect, implement a purposeful
visual/interaction improvement and compare equivalent before/after views. Preserve this as
self-review evidence; do not call it independent user feedback. Detailed reviews live under
the shared plan directory and are linked from index.md, not repeatedly injected into context.
