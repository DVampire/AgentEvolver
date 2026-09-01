# Factor Mining / Strategy Mining Agent — Requirements & Design

Status: draft (under discussion, not yet implemented)

## 0. Background

The goal is an agent that automatically mines quantitative factors and, from those
factors, automatically generates and optimizes trading strategies. The repo already
contains a related standalone project, `others/FactorStrategyLLM` (a working prototype
of factor/strategy mining, with an operator library, a backtest engine, and a metrics
suite), but after review we confirmed **its code will not be reused directly**. Instead
this is a from-scratch, bottom-up design built inside the AgentEvolver main framework
(`agentevolver/agent` + `environment` + `benchmark` + `tool`), aimed at being more
efficient and more reliable. The old project still serves as a rough reference for "what
this kind of system looks like," but the underlying engine — data representation, factor
representation, backtest execution — is redesigned entirely.

## 1. Requirements (confirmed direction)

Aligned with the user across three rounds of questions; conclusions:

| Decision point | Conclusion |
|---|---|
| Where it lives | Built fresh inside the AgentEvolver main framework (`agentevolver/agent` + `environment` + `benchmark`), not as a modification of `others/FactorStrategyLLM` |
| Top priority | **Reliability**: train/valid/test (in-sample/out-of-sample) split + objective stopping criteria, ahead of mining throughput and ahead of upgrading the agent's execution style |
| Asset scope | Multi-asset validation from day one — not satisfied with results tuned on a single asset (e.g. today's BTCUSDT-only setup) |
| Factor engine | Do not port `FactorStrategyLLM`'s implementation (per-symbol JSONL storage, Python-class factors, bar-by-bar event-loop backtesting); build a new engine optimized for mining throughput |

## 2. Core design principles

1. **Reliability is guaranteed architecturally, not by prompt reminders.** Whether the
   agent can "peek" at validation/test data should be a question of whether that data is
   physically present in its sandbox, not a matter of the system prompt asking it to
   behave.
2. **Stopping criteria must be objective.** Whether a factor/strategy is "good enough" is
   decided by preset thresholds plus out-of-sample re-verification. The LLM's own
   "I think this is good enough" is at most advisory input, never the sole basis for the
   decision.
3. **The strategy layer must not be able to lower the factor layer's evaluation bar.**
   When a strategy is missing some kind of factor, it can only *request* a new round of
   mining in the form of a diagnosis; any newly mined factor must still independently
   clear the factor layer's train/valid thresholds before it is admitted to the library —
   it cannot be waved through just because a strategy urgently needs it.
4. **Efficiency is an architectural concern, not a later optimization.** The essence of
   factor mining is evaluating as many candidates as possible within a compute budget. Any
   step that degrades into a Python-level loop (over symbols, over bars) becomes a
   bottleneck once amplified across dozens or hundreds of iterations, so vectorization has
   to be designed in starting from the data representation layer, not bolted on after.

## 3. Overall architecture

```
Benchmark (sole holder of the valid/test windows; sole authority on pass/fail)
   │  Publishes tasks: asset list + mining goal + threshold protocol
   │  After the agent submits, re-verifies on the held-out windows; scores only if all thresholds pass
   ▼
Agent (FactorMiningAgent / StrategyMiningAgent — standard think-and-act loop)
   │  Decides autonomously: inspect data → write factor/strategy expressions →
   │  call evaluation → check for duplicates → refine → decide when to submit
   │  The two agents co-iterate through a "diagnosis" protocol (see Section 13)
   ▼
Environment (factor_mining — holds market data + operator library + backtest engine + factor-library state)
   │  Exposes only the train split to the agent; the valid/test splits are physically absent from the sandbox
   ▼
Data layer (panel matrices + Parquet storage)
```

The boundaries between the three are deliberate, not an arbitrary application of the
framework template: Environment is "the stateful world the agent acts in," Agent is the
autonomous decision-maker, Benchmark is the single objective judge. The point of this
three-way split is to give "evaluation objectivity" a clear, single owner in the
architecture, rather than something scattered around and quietly bypassable.

## 4. Data representation layer: panel matrices

**Decision**: every field (`close`/`high`/`low`/`volume`/…) is stored as a
`(T timestamps × N assets)` wide matrix, with all assets aligned to a common timestamp
index. Missing values are left as NaN (no forward-filling to paper over a halted asset or
missing data — that absence is itself meaningful information, and filling it would
contaminate factors).

**Why not `FactorStrategyLLM`'s per-symbol JSONL:**
- Time-series operators (`ts_mean`/`delta`/…) call `.rolling(20).mean()` once on the whole
  wide table; pandas vectorizes across all columns (all assets) simultaneously — no
  `for symbol in symbols` loop needed.
- Cross-sectional operators (`rank`/`zscore`/`neutralize`) become row-wise (per-timestamp)
  operations across columns — `.rank(axis=1)` in one line. This is a capability the
  original design has none of (it's single-asset, time-series-only); here it comes for
  free from the data structure with no extra engineering.
- Multi-asset validation is no longer "run N single-asset backtests" — one matrix
  operation naturally covers every asset at once.

## 5. Storage layer: columnar (Parquet)

Replace JSONL: raw data is downloaded and written to Parquet, partitioned by symbol,
column-stored by field. Factor-mining iterations usually only need a handful of columns
(`close`/`volume`, …); columnar storage reads only the needed columns, cutting I/O by an
order of magnitude versus row-by-row JSON parsing — this directly affects how long one
mining iteration takes to turn around.

The data download layer (pulling OHLCV from exchanges/data vendors) can lean on mature
third-party libraries (e.g. ccxt for crypto) rather than a hand-rolled downloader; this
layer is not the throughput bottleneck and is not the focus of the redesign.

## 6. Factor representation layer: constrained expressions, not arbitrary Python classes

`FactorStrategyLLM` has the LLM generate an entire Python class (subclassing `Factor`,
writing an `async __call__`). That creates three problems: execution needs a real code
sandbox (arbitrary Python can run anything), structural dedup requires AST parsing (in
practice this was never built), and the LLM has a large surface area to get wrong
(imports, signature, types).

**Redesign**: a factor is an **expression string**, using only the function names
registered in the operator catalog, field names, and numeric constants, e.g.:

```
rank(ts_mean(close, 20) - ts_mean(close, 60))
```

Benefits:
- **Safety**: execution is just `eval(expr, {"__builtins__": {}}, operator_namespace)` —
  the namespace contains only registered operators and data columns; there is no need for
  a sandbox capable of running arbitrary Python.
- **Parseable deduplication**: an expression is naturally an AST. Before admission to the
  library, do a **structural comparison** (same operator tree, only the period parameter
  differs → flagged as a variant, not a brand-new factor) — this step touches no data and
  is essentially free, and it can reject a batch of duplicate candidates before spending
  any backtest compute.
- **Mutable**: genetic-programming-style mutation ("swap one child operator / change a
  parameter") operates directly on the AST, far more precise than asking an LLM to rewrite
  an entire Python class.

Strategies work the same way: by default represented as a "weighted factor scoring
formula" (e.g. `0.6 * zscore(factor_a) + 0.4 * zscore(factor_b)`, followed by a
threshold/ranking rule that decides position), which covers most systematic strategies
with the same safety and dedup properties. Strategies that genuinely need path-dependent
logic (stop-loss, state machines) fall back to Python code as the exception — there will
be few of these, so most scenarios don't pay for that flexibility.

## 7. Operator library: two tracks, both vectorized

The operator catalog is explicitly split into two classes:

- **Time-series operators** (`ts_mean, ts_std, delta, ts_rank, ts_corr, …`): take the wide
  table, compute a rolling window independently per column — native pandas rolling
  vectorization.
- **Cross-sectional operators** (`rank, zscore, demean, neutralize, winsorize, …`): take
  the wide table, compute across columns for each row (each timestamp).

This split is itself documentation: if a factor expression contains a cross-sectional
operator, the system immediately knows this is a "stock-picking / rotation" style factor;
if it only uses time-series operators, it's a "single-asset timing" style factor. No extra
metadata field is needed to distinguish them — the operator set itself signals the
factor's applicable paradigm, which directly routes how the strategy layer should consume
it (rotation vs. timing).

## 8. Factor evaluation layer

Because the data is a panel and evaluating a factor expression against the whole panel
yields a factor-value matrix in one pass, evaluation naturally supports two kinds of IC,
not just time-series IC:

- **Time-series IC**: for each asset column independently, correlate against that asset's
  forward-return column.
- **Cross-sectional IC**: at each timestamp, correlate the cross-asset rank of factor
  values against the cross-asset rank of forward returns, then average over time — the
  classic multi-factor stock-selection evaluation. This is a capability the data structure
  provides for free; no extra development needed.

The cost of evaluating one candidate factor is essentially a handful of matrix operations
and does not grow linearly with the number of assets. **Batch-evaluating N candidates**
means looping N times over matrix operations (the loop is over the number of candidates,
not "candidates × assets") — this is the core throughput improvement for mining.

**A new static-validation layer** (missing from the original design, and a real risk):
before execution, scan the factor expression for look-ahead bias (referencing a future
return column, negative time-shifts), rejected at the evaluation entry point. A factor
that uses future data will show an abnormally high IC on the train window, and without a
static scan it's very easy to mistake that for "found something great."

## 9. Factor evaluation report

Every evaluation produces a structured `FactorReport` (JSON + Markdown, both emitted):

- Factor expression, asset scope it applies to
- Time-series IC/RankIC/RankICIR per symbol × return period, plus cross-sectional IC
- NaN coverage, number of valid samples
- Maximum correlation against factors already in the library (numeric-level dedup, see
  Section 10)
- Train → valid metric decay (overfitting diagnostic)
- Threshold checklist (pass/fail per criterion)
- `triggered_by_gap`: if this factor was mined in response to a strategy-layer diagnosis
  (see Section 13), records the diagnosis ID, for causal traceability

Reports are stored alongside the factor library (expressions + versions + a contract
document), so every admitted factor can be traced back to how it was validated.

## 10. Factor-library deduplication (two-tier filtering)

Because factors are expressions rather than arbitrary code, dedup can be done in two
tiers, cheapest first:

1. **Tier 0 (structural, free)**: AST-level similarity comparison between the new
   candidate's expression tree and existing library factors — rejects candidates like "the
   same operator structure with a different period parameter" without running any data.
2. **Tier 1 (numeric, costs compute but is cheap given vectorization)**: on the train
   split, correlate the new candidate's factor values against every existing library
   factor; above a threshold (e.g. `|corr| > 0.7`) it's flagged as redundant and bounced
   back to the agent with the specific factor it's correlated with.

The two-tier design is itself an efficiency choice: use free structural comparison to
reject a batch first, then only spend evaluation cost on what's left, instead of running a
full backtest on every candidate before checking for redundancy.

## 11. Strategy layer

### 11.1 Representation

Default is a "scoring / combination model": a strategy is a weighted combination of
factors plus a position rule (threshold / ranking / linear mapping). Strategy generation
**may only reference factors already admitted to the library and independently
evaluated** — it cannot invent new factor calculations inline. This boundary guarantees
the strategy layer can never bypass the factor layer's evaluation standard.

### 11.2 Backtest engine: vectorized by default, event loop as an escape hatch

`FactorStrategyLLM`'s strategy backtest is a bar-by-bar `async __call__(df)` loop, rerun
hundreds of times per mining iteration — an obvious bottleneck. Redesigned as two engines:

- **Default: vectorized backtest** — the signal is a vectorized transform of the factor
  panel (not a per-bar call); the whole position vector is computed at once, and portfolio
  return = `position.shift(1) * forward_return - turnover * cost_pct`, cumulated via
  `cumprod` into an equity curve. The entire backtest is a handful of array operations, not
  a Python for-loop — one backtest drops from "seconds" to "milliseconds."
- **Escape hatch: event-driven engine** — only invoked when a strategy genuinely needs
  path-dependent state (e.g. pausing for a few days after a stop-loss triggers); slower but
  fully general. A strategy declares its own type (`vectorized`/`stateful`) and the
  environment picks the matching engine.

### 11.3 Evaluation

Structurally symmetric with the factor layer: the same train/valid/test three-window
protocol, the same multi-asset loop, the same structured `StrategyReport` output
(ARR/SR/SOR/MDD/CR, etc.). The report displays "strategy return" alongside "the IC of the
factors it depends on, over the same window," making it possible to tell whether a loss
comes from the strategy's weighting/rule design or from the underlying factor itself
having failed during that period.

## 12. Reliability mechanism: physical train/valid/test isolation + an anti-cheat evaluation bridge

Modeled on the anti-cheat pattern already in the repo for ProgramBench
(`agentevolver/tool/default/programbench_eval.py` +
the `eval_bridge_watcher` in `examples/run_programbench.py`):

- The agent runs inside a sandbox that **mounts only the train-split data files** — the
  valid/test splits are physically absent from the sandbox, so no amount of `bash`
  poking-around can find them. This is far more robust than "an Environment action simply
  doesn't expose that capability," which in principle can still be routed around (e.g. the
  agent guesses the data file path and reads it directly).
- When the agent wants a more realistic signal, it calls a **rate-limited** evaluation tool
  (e.g. 3–5 calls per task); the tool only writes a request to a bind-mounted bridge
  directory. A host-side watcher process, which holds the valid split, does the real
  scoring and returns only "pass/fail + summary metrics" — **never raw data or a value
  series**.
- The test split never participates in iteration at all; it is used exactly once, by the
  Benchmark, for the final score.

This mechanism is the concrete implementation of the "reliability first" top priority, and
is independent of whether the factor engine represents factors as expressions or Python
classes — it's a separate architectural decision from Sections 4–11.

## 13. Joint iteration protocol between factor mining and strategy mining

### 13.1 Why the strategy layer cannot directly dictate factor mining

If a strategy's missing factor type is allowed to relax the factor layer's evaluation
standard for a "just mine one for me" shortcut, the resulting factor is essentially
overfit to that one strategy, not a genuine signal — the factor library gets contaminated.
So the two agents can only communicate through one **explicit feedback channel**: the
strategy agent produces a "diagnosis," the factor agent treats the diagnosis as a *task
description* to mine against, and whatever it produces still has to independently clear
the train/valid thresholds — if it doesn't pass, it doesn't pass.

### 13.2 Protocol flow

```
Round 0:
  Factor mining (can run several directions in parallel) → independently evaluated → admitted → factor library v0
  Strategy mining: generate/optimize a strategy against v0, iterate to convergence → strategy performance S0 (on valid)
  If S0 falls short → the strategy agent produces diagnosis D0

Round k (k = 1..K, an outer budget, e.g. K=3):
  If S_{k-1} already meets the target, or there have been 2 consecutive rounds with no real improvement → stop
  The factor agent takes D_{k-1} as a targeted mining task → new factors independently evaluated → those that pass join factor library v_k
  The strategy agent regenerates/re-optimizes against v_k → strategy performance S_k; if still short, produces diagnosis D_k
  Compare S_k vs S_{k-1}: no real improvement counts toward the "no progress" streak
```

### 13.3 Design points for the diagnosis (feedback)

A diagnosis must be a **explainable description of a gap**, never a numeric target (it
must never say, e.g., "I need a factor with IC = 0.08" — that leaks the answer to the
factor agent, which will then just try to engineer something that barely clears the
threshold, rather than discovering a genuinely useful signal). Three typical diagnosis
types:

- **Coverage gap**: the library is missing an entire dimension of signal (e.g. all
  price-based, nothing volume- or volatility-based).
- **Insufficient effectiveness**: a related factor exists, but its IC is too weak to
  support the strategy.
- **Generalization failure**: the factor works on the training assets but is not
  significant on the assets the strategy actually targets.

### 13.4 Stopping condition (also objective)

Not decided by the LLM declaring itself satisfied. Two hard signals: **target met**
(strategy clears the threshold on the valid window), and **no-progress circuit breaker**
(2 consecutive rounds with no real improvement in strategy performance stop the loop even
if the outer budget hasn't been exhausted).

### 13.5 Traceability

Every factor's evaluation report carries a `triggered_by_gap` field, and every strategy
report carries a `used_factors` field; together they form a complete causal chain — "this
strategy uses these factors, and each factor was mined in round N in response to which
diagnosis." This matters both for reproducibility in quant research and for
after-the-fact debugging.

## 14. Integration points with the AgentEvolver framework

Follows the framework's existing module boundaries and registration conventions rather
than inventing a new one:

| Framework concept | Corresponding design | Existing pattern it mirrors |
|---|---|---|
| `Environment` (`agentevolver/environment/default/factor_mining/`) | Holds panel data, the operator library, the backtest engine, and factor-library state; actions include `list_symbols`/`get_operator_catalog`/`get_factor_library`/`run_factor_backtest`/`run_strategy_backtest`/`check_correlation`, all reading only the train split | `environment/default/ssh/` (`name` as a class field + `@environment_manager.action` + `ENVIRONMENT.md`) |
| `Agent` (`agentevolver/agent/actor/factor_mining_agent.py` / `strategy_mining_agent.py`) | A thin subclass reusing the base class's standard think-and-act loop rather than a bespoke fixed pipeline; mounts the `factor_mining` environment plus tools such as `bash`/`write_file`/`done` | `agent/actor/code_agent.py` |
| `Tool` (anti-cheat evaluation bridge) | Rate-limited request to the host for a valid-split score; returns only a pass/fail summary | `tool/default/programbench_eval.py` + the `eval_bridge_watcher` in `examples/run_programbench.py` |
| `Benchmark` (`agentevolver/benchmark/default/factor_mining.py`) | A Task = asset list + mining goal + threshold protocol; `eval()` re-evaluates on the private test split across every configured asset, scoring only if all thresholds pass | The `reset/step/eval` structure of `benchmark/default/programbench.py` |
| Joint-iteration driver | Implement the outer loop as a plain script first (not baked into either agent's internals); consider migrating to a formal `agentevolver/workflow` once the protocol has stabilized | — |

## 15. Relationship to `others/FactorStrategyLLM`

Its code is not ported, but some of its "proven-useful" layering ideas are carried
forward conceptually — strategies consuming only already-admitted factors, a persisted
factor library plus a contract document, the definitions of IC/RankIC/RankICIR and similar
metrics. Those conceptual choices are sound; only the underlying data structure, factor
representation, and backtest execution are reimplemented per Sections 4–11.

## 16. Open questions (for later discussion)

- The exact grammar of the expression DSL — should it support conditional branches
  (`if_else`) between factors, or stay strictly a composition of pure functions?
- Whether mutation / genetic-programming search belongs in v1, or whether v1 should stay
  with "LLM batch generation + two-tier dedup" only.
- Default values for the outer-loop budget `K` and the no-progress circuit-breaker
  threshold in the joint-iteration protocol — likely need a few empirical runs to
  calibrate.
- Whether cross-sectional factors need their own dedicated strategy templates
  (rotation/stock-picking), or whether v1 should stay with time-series factors + timing
  strategies only, leaving cross-sectional for v2.
