# Factor and strategy research tasks

These task folders describe research products and their acceptance criteria. They are
independent of the implementation framework and can be used with different research systems.

## Signal Foundry

[The project brief](signal_foundry/task.html) asks for joint strategy and factor research
on one stock. Design complete mechanisms: factor roles, combinations and their fitting,
entry, holding, exit, sizing and risk. Evaluate their evidence together and revise the
component implicated by the diagnosis. Parameter calibration and stability checks support
these mechanisms. They do not replace factor-combination or policy research.

The researcher chooses batch sizes, active routes, thresholds and statistical methods from
training evidence and expected information/cost. No fixed research counts or numerical
admission thresholds are prescribed. Different strategies may use different factor sets;
conditional/group inputs need evidence for their actual use, not universal standalone IC
passes. Preserve failed claims, exact versions and all declared changes in matched controls.

Deliver **one continuous report page** with both factor and strategy evidence visible through
scrolling and in-page anchors. JSON files retain definitions, numeric results, pool decisions,
comparisons and provenance. The researcher analyzes JSON directly; HTML/CSS/JS visualize it
for readers. Keep versioned results and reports discoverable through an index. No browser
operation, separate stage pages or mandatory publication sequence is part of the research.

## Files and research constraints

- `task.html`: product goals, reader journeys and research-quality expectations.
- `study.json`: market scope, dates, execution assumptions, factor-role qualification,
  exploration guidance and the domain evidence standard.

The v13 study uses **TSLA** daily observations over **2016-09-12 through 2026-09-11**.
**Train: 2016-09-12–2023-12-31. Test: 2024-01-01–2026-09-11.** Discovery, fitting and
selection all occur in train, with agent-designed chronological rolling windows. Factor
and combination fits respect actual outcome availability at each cutoff. Both environments,
comparisons and reports consume the same plan; there are no fixed calendar-year folds.

Acquire train first. Freeze the selected complete strategy, fitted states/refit policy,
benchmarks and evidence standard before final test. This historical test was already viewed
in earlier project experiments; study.json preserves that exposure. A new run does not create
unseen confirmation. Interpret any new result on it as a historical diagnostic.

Distinguish rolling fold-reset scores from continuous-account results, with matching
benchmarks, costs and declared fitting/account transitions. Agent-designed uncertainty and
support criteria are recorded before their comparisons; failed required evidence cannot be
rescued by changing the standard afterward. No fixed 95% interval-bound requirement remains.

Worker receipts record actual completion before agent-side collection. Planned requests are
not observations, and stopping reconciles finished results independently of round summaries.
Report snapshot state separately from the current run's completion/interruption state.

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

Every experiment starts with fresh research records. Supplied prior-exposure disclosures
remain binding; repeating historical data does not provide independent new confirmation.

[Running and implementation guide](../../../docs/demos/factor_strategy_mining.md).

Formal strategies are multi-factor combinations with at least two distinct, actually used
factors. Design their factors and policies together and refine each selected route. Single-factor
controls, ablations and benchmarks explain contribution but do not count as candidates or enter
the working pool/final selection. The combination and factor count remain hypothesis-specific.
