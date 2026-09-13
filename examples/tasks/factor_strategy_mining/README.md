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

These responsibilities recur together. Explore multiple mechanisms, give strategies their
own relevant factor versions, and revise factors when consumer evidence identifies an
opportunity. Review and refine shortlisted routes individually, preserving rejected trials
and parent/candidate comparisons. Formula names and position-size changes alone do not
establish diversity. Factor qualification follows its declared role and consumer scope.

The experience should make an investigation easy to follow: inspect a factor, understand
its selection, trace it into a strategy and explore the trades behind a drawdown. Deliver
**one continuous report page**, with both stages visible through scrolling and in-page
anchors. Do not split them into routes or tabs. Keep candidate inventories, split comparisons,
metric/chart definitions, research decisions and downloadable evidence together. Stage-one
and stage-two publications are versions of the same product, not separate report pages.

## Files and research constraints

- `task.html`: product goals, reader journeys and research-quality expectations.
- `study.json`: market scope, dates, execution assumptions, factor-role qualification,
  exploration requirements and the domain evidence standard.

The v5 study uses NVDA daily observations through **2026-09-11**, training in 2016–2020,
validation in 2021–2023 and final test from 2024-01-01 through the cutoff. It replaces
fixed CAGR/Sharpe finish targets and trial/round/patience ceilings with evidence-based
research judgment. The researcher repeatedly discovers and refines factors and strategies,
reviews every shortlisted route and justifies completion against research quality.

The skill defines how to operationalize the standard and record evidence, counterevidence,
remaining investigations and a continue/complete decision. Unresolved material questions
require further feasible research. A report release, first eligible candidate or candidate
count does not certify completion. Runtime limits yield interrupted research, not success.

Use a documented public-data basis with source/adjustment limitations and separate strict
data qualification. Keep causal chronological fitting and one frozen final evaluation.
Further work after test exposure is exploratory; a revised strategy needs genuinely unused
observations for confirmation. A thorough negative/inconclusive conclusion may complete the
research without claiming a successful strategy. A failed test alone does not justify closure.

Every experiment starts independently with fresh records and implementations. Repeating
historical data does not provide independent confirmation on new market observations.

[Running and implementation guide](../../../docs/demos/factor_strategy_mining.md).
