# Research protocol

## Freeze before performance search

Use [metrics-and-evaluation.md](metrics-and-evaluation.md) for exact formulas, scopes,
aggregation, null states, numerical fixtures and evidence-driven iteration. Bind that
versioned contract to both environments and the report before researching performance.

Read the study specification and write a content-hashed contract before evaluating candidates.
It fixes data identity, market/calendar, session frequency, dates, fit/score boundaries,
horizons, execution timing, adjustments, cost model, factor admission, strategy selection,
final acceptance, experiment budgets and insufficient-evidence rules. Compute additional
diagnostics freely on research splits; freeze any extra selection criterion before using it.
Do not weaken the task's supplied thresholds. Record a data incompatibility as a blocker.

Interpret date boundaries as inclusive exchange-local session dates and translate them to
the provider's timestamp/range semantics explicitly. When completed_sessions_only is true,
exclude unfinished sessions and verify that all required completed sessions through the
declared cutoff are present. Record missing final bars as incomplete acquisition; do not
silently shorten the study. Never extend the frozen cutoff automatically during a run.

Hash training/validation snapshots when acquired. If test retrieval is deferred, freeze
its provider, symbol, interval, feed, session, adjustment and source-revision policy instead
of inventing a not-yet-known file hash. After submission freeze, log the single permitted
retrieval and bind its returned snapshot hash before calculating metrics. Preserve that
snapshot for exact replay; later refetches cannot replace it silently. This binding receipt
extends provenance without changing the frozen research choices.

The default Signal Foundry thresholds are illustrative product acceptance targets, not a
prediction that NVDA can satisfy them. Historical stock selection is itself a source of bias.
Report that limitation and do not generalize a single-stock result into universal market alpha.

## Chronology and exposure

Use chronological train, validation and test ranges. Within validation, use the specified
expanding annual folds; at each fold fit using strictly earlier observations, purge training
labels whose outcomes overlap the evaluation boundary, and respect the declared session gap.
Record actual first/last fit and score timestamps after exclusions and warm-up. A gap is in
trading sessions, not calendar days. Gaps must also cover the actual label/execution horizon;
never truncate a label into another split. Past observations may warm up trailing features,
but may not be scored twice or used to fit transformations on later observations.

Factor direction, primary horizon, quantile boundaries, scaling, clipping, imputation,
feature selection, regime classifier and strategy weights are fitted inside each training
fold. Do not preprocess the whole dataset before splitting. An annual refit may use previous
validation years if this expanding-window rule was predeclared; report this and retain all
validation selection attempts. Never shuffle time-series samples.

Validation is a tuning resource, not fresh independent evidence after each look. Count every
distinct candidate/parameter trial and every validation submission durably before execution.
Bound a batch, count rejected/error candidates, deduplicate identical replays and never reset
counts on restart. Choose the final strategy using the frozen validation ranking (eligibility
first, then net Sharpe, then lower drawdown/complexity with a deterministic tie break).
Report search breadth and uncertainty, not only the winning row.

### One joint final evaluation

Before opening test, freeze a bundle containing the selected factor library, factor directions
and horizons, preprocessing/refit policy, one strategy, engine versions, cost scenarios, data
fingerprints, metric definitions and acceptance rules. A permitted refit on all pre-test data
must be specified beforehand and exclude boundary-overlapping labels. Freeze its resulting
parameters before reveal. Do not try all validation winners on test and pick the best.

Evaluate that bundle once for both sections of the continuous report. The factor section can
show the selected factors' test diagnostics only now; these are descriptive results, not
another factor-admission pass.
The strategy section compares the single selected strategy with the fixed benchmarks and cost
scenarios. Do not use test ablations, cost sliders or extra horizons to select a new strategy.

Record the test-attempt marker before reading/evaluating test. Allow idempotent retrieval of
the same stored result. A crash after exposure does not reopen tuning; report the interruption
and resume only the exact frozen computation if its exposure/identity can be established.
Any data/accounting/engine fix after reveal invalidates confirmatory interpretation on that
test. Preserve the old result and label corrected same-test calculations diagnostic.

A failed or inconclusive test is a legitimate unsuccessful outcome. Further research can
continue on train/validation, but passing research acceptance then needs genuinely unseen
future observations under a newly frozen protocol. Renaming a study, resplitting seen history,
changing the seed or choosing a different endpoint does not restore independence.

### Be precise about the trust boundary

The solo demo defaults to `holdout_control=protocol_only`: it has authoring tools and a data
connector. Deferring test retrieval, omitting test from previews and using a durable ledger
reduce accidental leakage, but an editable ledger or agent-authored environment does not
stop the agent reading/refetching data. Call this an audited self-evaluation, not enforced
isolation. Pretrained models may already know historical market events or returns.

An independently isolated evaluation requires an evaluator outside the researcher's write
and data-access authority. The existing `factor_mining` Benchmark/bridge is a reference for
that boundary, not automatically mounted here. Do not claim its protections apply to this
demo. If the task explicitly requires enforced isolation, establish and test that boundary
first or report it unavailable. UI lock icons and hashes alone do not establish it.

## Factor evaluation

For a single stock, evaluate time-series Pearson IC and Spearman RankIC between an available
factor value and a future tradable return, by horizon and fold. Use the same corporate-action
and next-open convention as the strategy engine. Specify label endpoints explicitly, e.g.
the total return from the next open through the open h sessions later. Do not correlate with
same-bar returns or call a single-stock statistic cross-sectional IC.

The default admission RankIC is the equally weighted mean of the three annual validation
fold RankICs at the training-selected primary horizon and direction. Report each fold and
the pooled statistic separately. Require the study's positive-fold count, pooled coverage and
total non-overlapping-label minimum across scored validation folds, with finite evidence in
each fold. A constant factor, zero-variance return, too-small sample or
undefined correlation is null/inconclusive, never zero by convenience or a passing infinity.

Show training-fitted quantile response, horizon decay, rolling/block stability, regime
coverage and absolute factor correlations on aligned research observations. Prevent sign
flips selected on validation/test and duplicate formulas disguised by affine transforms.
Admission ties prefer simpler factors with stable evidence; keep the rejection rationale.
Single-factor diagnostic returns are not a strategy unless execution and costs are defined.

Overlapping labels and serially correlated returns are not independent observations. Provide
a dependence-aware uncertainty method, its block/window choices and effective/non-overlapping
sample counts. Track multiple testing. If implementing Deflated Sharpe or another correction,
validate its numerical inputs and assumptions; otherwise mark it unavailable rather than
inventing a significance score. A positive point estimate is not proof of persistent alpha.

## Strategy accounting and numerical definitions

The default is daily long-or-cash, no leverage/shorts: a signal from a completed close can
first trade at the next tradable open. Carry shares and cash with raw execution prices,
explicit split/share changes and dividend entitlement/payment rules. If precise dividend
payment records are unavailable, declare and validate a consistent total-return convention;
do not silently credit both adjusted prices and dividend cash. Publish assumptions and
unresolved approximations. No intrabar stop execution can be inferred from daily OHLC alone.

Charge commission and slippage on actual traded notional at every entry, rebalance and exit,
including terminal liquidation. Account for drift, position limits and affordability after
fees. Benchmark buy-and-hold uses the same investment dates, corporate actions, costs and
liquidation convention. Cash and Sharpe reference rates are the explicit study assumptions.
Fail if a held position lacks a required execution/mark price; do not replace it with zero PnL.

Freeze metric definitions in the engine/report schema:

- Net return comes from the reconciled equity ledger; daily return is consecutive equity
  ratio minus one. Include initial capital in the equity series and drawdown peak.
- CAGR uses the declared scored-session annualization, and volatility/Sharpe use the same
  frequency and excess-return convention. Label annualization, sample count and ddof.
- Drawdown is peak-to-trough equity loss; report it as a positive loss fraction for the
  maximum-drawdown gate. Show recovery duration and unrecovered drawdowns.
- Define Sortino downside target, Calmar denominator, turnover denominator and whether trade
  metrics use fully closed round trips. Position resizes cannot inflate completed-trade count.
- Cost sensitivity repeats the frozen strategy/engine at the predeclared cost multiplier;
  it does not retune. Report price-only versus total-return differences when relevant.
- Undefined ratios, insufficient scored sessions/trades, failed accounting checks or missing
  metrics cannot pass acceptance. Every final criterion must have a value, rule and verdict.

Compare train/validation/test on their actual scored intervals; do not stitch them into a
misleading continuous out-of-sample curve. Distinguish return targets from benchmarking:
the default requires net test CAGR, Sharpe, drawdown, sample size, comparison with buy-and-hold
Sharpe and positive stressed-cost return jointly. A positive training result or a visually
appealing report does not satisfy these gates.

## Primary references

- [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html): chronological folds and the gap parameter; label-aware purging still needs explicit design.
- [Bailey and López de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf): selection bias and non-normality matter when judging the best of many trials.
