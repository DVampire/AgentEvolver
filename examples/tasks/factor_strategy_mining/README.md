# Factor and strategy research tasks

These task folders describe research products and their acceptance criteria. They are
independent of the implementation framework and can be used with different research systems.

## Signal Foundry

[The project brief](signal_foundry/task.html) asks for an interactive workbench with two
connected research stages:

1. Discover and evaluate interpretable factors from a stock's price and volume history;
   publish the Factor Observatory section with definitions, measured effects, selection
   evidence and rejected hypotheses.
2. Turn selected factors into strategies; extend the same page with the Strategy Atelier's
   trading rules, returns, risk, costs, trade records and final evaluation.

The experience should make an investigation easy to follow: inspect a factor, understand
its selection, trace it into a strategy and explore the trades behind a drawdown. Deliver
**one continuous report page**, with both stages visible through scrolling and in-page
anchors. Do not split them into routes or tabs. Keep candidate inventories, split comparisons,
metric/chart definitions, research decisions and downloadable evidence together. Stage-one
and stage-two publications are versions of the same product, not separate report pages.

## Files and research constraints

- `task.html`: product goals, reader journeys, expected reports and acceptance requirements.
- `study.json`: market scope, date ranges, trading assumptions, research budgets and
  numerical criteria for this particular study.

The default study uses NVDA daily observations through **2026-09-11**, with training in
2016–2020, validation in 2021–2023 and final test from 2024-01-01 through the cutoff.
Only completed trading sessions are eligible. Exact boundaries, gaps and numerical
criteria are authoritative in [study.json](signal_foundry/study.json).

Iterations use training and validation. Final-test results must not guide factor or
strategy selection. An unsuccessful or inconclusive study must remain visible in the
reports; a useful research product does not imply a profitable strategy.

Research budgets count hypotheses and validation exposure, rather than execution steps
or model tokens. Methods and implementation details can be designed for the chosen system
without changing the product's research scope or weakening its acceptance criteria.

[Running and implementation guide](../../../docs/demos/factor_strategy_mining.md).
