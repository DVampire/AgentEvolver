# Factor expressions, operators and the DataFrame contract

Read before implementing or revising factor calculations. Mine expressions and economic
hypotheses; use `scripts/factor_expression.py` to validate and compile numerical definitions.
The script needs pandas/numpy and makes no model calls, downloads or market evaluations.
The Factor Environment loads generated code, fits any separate training-only state, scores
factors and issues qualification receipts. Compilation is not evaluation or admission.

## Uniform input and output

```python
compute_factors(data: pandas.DataFrame) -> pandas.DataFrame
```

- Input is one instrument with a unique, increasing `DatetimeIndex` of observations and
  real numeric columns. Default observed fields are `open, high, low, close, volume`.
  Unused columns are allowed; required fields are recorded by the compiler. Declare the
  adjustment/volume basis and availability separately in the research contract.
- Output has exactly the same index/order/row count and one floating-point column per
  factor version. Values are scores, not positions or future labels. The caller supplies
  context before scoring; do not drop warm-up rows, forward-fill all results or replace NaN
  with zero. NaN carries missing/warm-up/undefined evidence into the coverage checks.
- Inputs are never mutated, silently sorted or aligned by row position. Multi-instrument
  frames are rejected when identifiable; explicitly partition upstream, evaluate each
  instrument's chronological frame and reassemble. Cross-sectional operators are a separate
  capability requiring a declared same-time universe; they are not rankings through time.
- The public boundary uses DataFrames. Individual operators work on aligned Series/scalars
  internally, so composing expressions does not require constructing intermediate DataFrames.

Only declare observed input fields whose values were available at the signal time. A spec
may extend `fields` for justified data; adding `future_return` to that list does not make it
causal. Label computation, train-fitted transforms and strategy decisions remain separate.

## Compile a factor or a library

```bash
python {skill_dir}/scripts/factor_expression.py operators
python {skill_dir}/scripts/factor_expression.py check --name F_trend@1 \
  --expression 'close / delay(close, 20) - 1'
python {skill_dir}/scripts/factor_expression.py compile --name F_trend@1 \
  --expression 'close / delay(close, 20) - 1' --output /absolute/workspace/factors/trend_v1.py
python {skill_dir}/scripts/factor_expression.py compile --spec /absolute/factor-spec.json \
  --output /absolute/workspace/factors/library_v2.py
```

The batch spec contains `factors` (versioned column name → expression) and optional `fields`:

```json
{
  "factors": {
    "F_trend@1": "close / delay(close, 20) - 1",
    "F_range@1": "(close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20))"
  }
}
```

Examples illustrate syntax, not a required factor inventory. Each new mechanism still needs
a hypothesis, role, falsification condition and a counted evaluation. Expressions may use
numeric literals, declared field names, listed function calls, parentheses, `+ - * / **`,
unary `+ -`, and single comparisons `< <= == != >= >`. Calls accept the documented positional
or keyword arguments. Use `and_op`, `or_op`, `not_op`, `if_else` for vector logic, e.g.
`or_op(close > delay(close, 1), volume > ts_mean(volume, 20))`. Python `and/or/not` and
bitwise `& | ~` are unsupported. Attributes, subscripts, imports, comprehensions and
arbitrary calls are rejected. Check the whole proposed spec before archiving versions.
Windows/lags are literal integers; negative lags are forbidden. There is no implicit eval.

Compilation writes the requested Python module and a sibling `_factor_ops_<hash>.py`.
Copy both files together into the capability/workspace; no dependency on the source skill
path or `others/FactorStrategyLLM` remains. Load the generated module using an ordinary local
module import or importlib and call `compute_factors(frame)`. Keep the exact code/runtime
hashes in the environment's candidate/cache identity. Existing differing artifacts are not
overwritten: use a new version path. Repeating an identical compilation is idempotent.

When deriving a fixture or factor revision by copying a parent bundle, give the new library
a new output filename and bind the compiler receipt's `code_path`/`runtime_path`. The copied
parent library is still immutable. Do not compile a different spec over it, rename archived
evidence to disguise replacement, or edit generated code to bypass the conflict. Check a
new spec with `check --spec ...` before emission and retain subprocess stderr on failure.

Revise the expression/specification and recompile, including synthetic fixtures. Do not
modify generated Python with string replacement: numeric text can also occur in runtime
filenames, hashes and metadata. Use `FACTOR_SPEC["factors"][versioned_name]["expression"]`
or its compile receipt for the canonical formula. Report adapters must inspect the actual
result schema instead of assuming an `expression` key on an environment's custom spec.

`FACTOR_SPEC` records canonical expressions, required fields, operator version/runtime hash
and conservative `lookback_rows` (past context beyond the current row). Stateful operators
report null lookback and require the same complete past prefix/state policy on every replay.
This metadata is not an admission receipt or sample-count estimate. Canonical expressions
normalize infix/alias syntax; other algebraic equivalences still need redundancy review.
Renaming an output does not create a new research hypothesis or reset trial accounting.

For a joint research batch, collect the strategies' required expressions into one spec,
with a separate mapping from each strategy version to its exact factor columns and roles.
Compile and evaluate identical definitions once per semantic data/fitting scope. A new factor
revision gets a new column/version and output path; other strategies keep their old bindings.
Keep compiler receipts with the factor definitions and round/evaluation IDs in the catalog.
Compilation failures are diagnostic errors, not counted numerical research or rejected alpha.

## Numerical conventions and extension

Trailing windows include the current observed row and require all window values by default;
exceptions are explicitly listed. Standard deviation/covariance use ddof=1. Invalid real
arithmetic and zero denominators yield NaN, not an epsilon-shifted return or infinity.
Comparisons and vector logic preserve missing operands; `is_nan` intentionally exposes them.
Future perturbations must never change earlier factor outputs. Availability after close
still means the earliest allowed execution is the next open.

The reference `others/FactorStrategyLLM/src/operator/ops.py` informed the operator vocabulary,
but its full-Series rank/zscore/quantile/normalize are not causal on a single-stock history.
Use their explicit trailing counterparts. This implementation also defines most-recent
extrema ties, bounded recent-value lookup, state resets and missing-condition behavior;
do not assume every similarly named external operator has these semantics.

If a useful hypothesis needs a missing operation, specify its causal input/output and extend
the versioned runtime through the shared self-evolution workflow. Update the registry/table,
test hand-computed results, missingness, prefix invariance and generated-code parity, then
compile a new candidate version. Never approximate an unsupported operation silently or
restrict economic exploration to this initial set. Separate stateful training-fit transforms
from pure trailing expressions; do not add whole-dataset fitting to bypass that boundary.

The table below is generated from the executable registry by the `operators` command.

<!-- operator-table -->
| Operator | Parameters | Semantics |
| --- | --- | --- |
| `abs` | `x: value` | Absolute value. |
| `reverse` | `x: value` | Negation. |
| `sign` | `x: value` | -1, 0 or 1; missing stays missing. |
| `log` | `x: value` | Natural logarithm; nonpositive input is missing. |
| `sqrt` | `x: value` | Square root; negative input is missing. |
| `is_nan` | `x: value` | 1 for missing, otherwise 0. |
| `not_op` | `x: value` | 1 for zero, 0 for nonzero; missing stays missing. |
| `add` | `x: value, y: value` | x + y; missing values propagate. |
| `subtract` | `x: value, y: value` | x - y. |
| `multiply` | `x: value, y: value` | x * y; missing values propagate. |
| `divide` | `x: value, y: value` | x / y; zero denominator yields missing, without epsilon adjustment. |
| `power` | `x: value, y: value` | x ** y; invalid or nonfinite real results become missing. |
| `signed_power` | `x: value, y: value` | sign(x) * abs(x) ** y. |
| `min` | `x: value, y: value` | Elementwise minimum; missing propagates. |
| `max` | `x: value, y: value` | Elementwise maximum; missing propagates. |
| `lt` | `x: value, y: value` | x < y. |
| `le` | `x: value, y: value` | x <= y. |
| `eq` | `x: value, y: value` | x == y. |
| `gt` | `x: value, y: value` | x > y. |
| `ge` | `x: value, y: value` | x >= y. |
| `ne` | `x: value, y: value` | x != y. |
| `and_op` | `x: value, y: value` | Nonzero x AND nonzero y; either missing yields missing. |
| `or_op` | `x: value, y: value` | Nonzero x OR nonzero y; either missing yields missing. |
| `if_else` | `condition: value, yes: value, no: value` | Select yes for nonzero condition, else no; missing condition yields missing. |
| `delay` | `x: series, period: lag` | Past shift by period rows; zero allowed, negative forbidden. |
| `ts_delay` | `x: series, period: lag` | Past shift by period rows; zero allowed, negative forbidden. |
| `delta` | `x: series, period: lag` | x[t] - x[t-period]. |
| `ts_delta` | `x: series, period: lag` | x[t] - x[t-period]. |
| `ts_sum` | `x: series, period: window` | Trailing sum. |
| `ts_mean` | `x: series, period: window` | Trailing mean. |
| `ts_min` | `x: series, period: window` | Trailing minimum. |
| `ts_max` | `x: series, period: window` | Trailing maximum. |
| `ts_product` | `x: series, period: window` | Trailing product. |
| `ts_stddev` | `x: series, period: window` | Trailing sample standard deviation (ddof=1); needs at least two rows. |
| `ts_std_dev` | `x: series, period: window` | Alias of ts_stddev. |
| `ts_rank` | `x: series, period: window` | Current value's percentile rank in trailing window; average ties / period. |
| `ts_argmax` | `x: series, period: window` | Rows since the most recent maximum in trailing window; current = 0. |
| `ts_argmin` | `x: series, period: window` | Rows since the most recent minimum in trailing window; current = 0. |
| `ts_arg_min` | `x: series, period: window` | Alias of ts_argmin. |
| `ts_zscore` | `x: series, period: window` | (x - trailing mean) / sample std; zero variance yields missing. |
| `ts_av_diff` | `x: series, period: window` | x - trailing mean; requires a complete window. |
| `ts_normalize` | `x: series, period: window` | (x - trailing min) / (trailing max - min); constant window is missing. |
| `ts_corr` | `x: series, y: series, period: window` | Trailing Pearson correlation / sample covariance (ddof=1), on complete paired windows. |
| `ts_covariance` | `x: series, y: series, period: window` | Trailing Pearson correlation / sample covariance (ddof=1), on complete paired windows. |
| `ts_quantile` | `x: series, period: window, q: fraction` | Trailing quantile with linear interpolation. |
| `kth_element` | `x: series, period: window, k: k` | k-th most recent finite value in trailing window, k=1 includes today; partial windows allowed. |
| `last_diff_value` | `x: series, period: window` | Most recent finite past value different from current within window; partial windows allowed. |
| `days_from_last_change` | `x: series` | Rows since last adjacent finite change; first valid row or row after missing starts at 0. Stateful. |
| `hump` | `x: series, limit: positive` | Cap each output's change from prior output to +/- limit; missing resets state. Stateful. |
