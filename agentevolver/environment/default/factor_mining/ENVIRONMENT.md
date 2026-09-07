---
name: factor_mining
description: Train-only factor and strategy research with independent held-out validation.
version: 1.0.0
---
# Factor research

Use `describe` and `get_operator_catalog` first; `inspect_data(offset=0, rows=5)`
returns a bounded training sample and field distributions. Data consists of aligned OHLCV
panels; missing bars stay missing. Expressions permit field names, arithmetic,
comparisons and catalog operators only. Windows are positive integer literals.

A factor spec is `{name, expression, direction: 1 or -1, rationale,
triggered_by_gap: null or diagnosis_id}`. Example:
`{"name":"momentum","expression":"delta(close, 5) / delay(close, 5)","direction":1}`.
`run_factor_backtest(candidates=[...])` returns train diagnostics; passing train
is not admission. `check_correlation(candidate=...)` rejects variants/redundancy.
Use `validate(kind="factors", candidates=[...])` sparingly: the host independently
checks train + valid, decay and duplicates before admitting factors.

`get_factor_library` returns authoritative factors, validated strategies,
diagnoses and remaining validation calls. Strategies may use ONLY names of
admitted factors, never raw OHLCV or unvalidated inline factors. A strategy spec
is `{name, expression, position_rule: "rank"|"threshold"|"linear", threshold: 0,
long_only: false, engine: "vectorized"|"stateful", stop_loss: 0.05, cooldown: 5,
rationale}`. Defaults may be omitted. Example: `{"name":"rotation",
"expression":"zscore(momentum)","position_rule":"rank"}`.

`run_strategy_backtest(strategy=...)` runs train only. Gross exposure is capped
at one; costs include turnover and final liquidation. Scores from close[t] trade
at open[t+1], earning the following open-to-open return. The stateful engine adds
a close-observed stop and next-open exit with cooldown; it accepts no Python code.
`validate(kind="strategy", candidates=[one_strategy])` gives independent valid
metrics. Each factors batch or strategy costs one validation call; library reads
and diagnoses are free. No action can read the final test split.

When strategy results expose a gap, call `record_diagnosis(diagnosis={id, kind,
description, evidence, requested_direction})`. Kind is `coverage_gap`,
`weak_signal`, or `generalization_failure`. Describe an explanatory research
direction, not a requested numeric metric. New factors cite that id and still
must pass the original acceptance rules. Do not claim a profitable real-world
strategy from synthetic data or a training result.
