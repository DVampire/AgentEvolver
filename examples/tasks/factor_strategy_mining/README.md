# Factor and strategy research tasks

These task folders describe research products and their acceptance criteria. They are
independent of the implementation framework and can be used with different research systems.

## Signal Foundry

[The project brief](signal_foundry/task.html) asks for joint strategy and factor research
on one stock. Start with roughly ten diverse strategies and the factors each needs; evaluate
factor diagnostics, complete trading policies and their contributions together. Select a
working pool by evidence, diversity and improvement potential, then refine the factors/rules
while adding new hypotheses. Different strategies may use different factor sets.

About 100 evaluated factor definitions and 20–30 distinct strategy hypotheses are cumulative
exploration references across rounds, not candidate minimums, a required first batch, a passing
quota or a search ceiling. Parameter-only variants and renamed formulas remain trials without increasing
mechanism coverage. Review every shortlisted route and preserve failed revisions.

Deliver **one continuous report page** with both factor and strategy evidence visible through
scrolling and in-page anchors. JSON files retain definitions, numeric results, pool decisions,
comparisons and provenance. The researcher analyzes JSON directly; HTML/CSS/JS visualize it
for readers. Keep versioned results and reports discoverable through an index. No browser
operation, separate stage pages or mandatory publication sequence is part of the research.

## Files and research constraints

- `task.html`: product goals, reader journeys and research-quality expectations.
- `study.json`: market scope, dates, execution assumptions, factor-role qualification,
  exploration guidance and the domain evidence standard.

The v9 study uses **TSLA** daily observations over the trailing ten-year window
**2016-09-12 through 2026-09-11**. Training covers 2016-09-12–2020-12-31,
validation covers 2021–2023, and final test covers 2024-01-01 through the cutoff.
Acquire train/validation first and defer test acquisition until the frozen final evaluation.
Completion follows evidence-based research judgment, without fixed CAGR/Sharpe finish
targets or trial/round/patience ceilings. The researcher repeatedly discovers and refines factors and strategies,
reviews every shortlisted route and justifies completion against research quality.

The researcher decides whether to continue from evidence, recent improvement history and
the expected information/cost of the next experiment. Supported objectives or repeated
ineffective improvements with low expected value can justify completion; neither exhaustive
search nor budget exhaustion is required. The skill guides the evidence record. A report
release or candidate count alone does not certify completion. Actual runtime limits yield
interrupted research.

Use a documented public-data basis with source/adjustment limitations and separate strict
data qualification. Keep causal chronological fitting and one frozen final evaluation.
Further work after test exposure is exploratory; a revised strategy needs genuinely unused
observations for confirmation. An evidenced negative/inconclusive conclusion may complete
research without claiming a successful strategy. If no candidate qualifies, test can remain
unexposed; the report explains why. A failed test alone does not decide whether to continue.

Every experiment starts independently with fresh records and implementations. Repeating
historical data does not provide independent confirmation on new market observations.

[Running and implementation guide](../../../docs/demos/factor_strategy_mining.md).

Formal strategies are multi-factor combinations with at least two distinct, actually used
factors. Design their factors and policies together and refine each selected route. Single-factor
controls, ablations and benchmarks explain contribution but do not count as candidates or enter
the working pool/final selection. The combination and factor count remain hypothesis-specific.
