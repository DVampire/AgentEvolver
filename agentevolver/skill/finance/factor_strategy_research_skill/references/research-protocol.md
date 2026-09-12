# Research protocol

## Freeze before performance search

Use [metrics-and-evaluation.md](metrics-and-evaluation.md) for exact formulas, scopes,
aggregation, null states, numerical fixtures and evidence-driven iteration. Bind that
versioned contract to both environments and the report before researching performance.

Read the study specification and write a content-hashed contract before evaluating candidates.
It fixes data identity, market/calendar, session frequency, dates, fit/score boundaries,
horizons, execution timing, adjustments, cost model, factor admission, strategy selection,
submission readiness, final acceptance, experiment budgets and insufficient-evidence rules.
Compute additional diagnostics freely on research splits; freeze any extra selection criterion before using it.
Do not weaken the task's supplied thresholds. Apply the study's explicit data_policy when
classifying incompatibility: an authorized adjusted/public-data research mode may proceed
with strict data qualification unmet. Unsupported scope changes still require user input.

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

### Submission readiness before test access

Validation eligibility creates a shortlist; it does not authorize a final submission.
Read `acceptance.final_submission` when supplied. Freeze its interpretation before search
and implement the checks in the research environment using the same saved metrics and
gate semantics as ordinary evaluations. Retain separate eligibility and readiness results.
Do not add a launcher-specific completion shortcut or let the website decide readiness.

The supplied Signal Foundry policy requires all of the following, with result IDs and
unrounded values. For another study, apply its declared submission contract:

- The strategy passes `acceptance.validation_strategy` and uses admitted factor versions.
- Every `acceptance.final_test_strategy` criterion also passes on pooled **validation**
  results: net CAGR, net Sharpe, maximum drawdown, completed round trips, scored sessions,
  Sharpe advantage over the matched buy-and-hold benchmark, and stressed net total return.
  Reference those thresholds rather than maintaining another numerical copy. Keep the scope
  explicitly validation; these checks are readiness evidence, not final-test success.
  Unknown/missing/nonfinite values or insufficient sample support cannot pass.
- The predeclared research-only robustness checks are executed and interpreted: fold/regime
  stability, required cost stress, and relevant parameter-neighborhood/ablation comparisons.
  Bind the same candidate version and inputs, count those trials, and address contradictory
  evidence. Freeze any additional pass thresholds before selection; do not invent significance
  or demand an unbounded parameter sweep. A missing engine operation is work to implement.
- A search-closure record explains why the selected version is ready: tested hypotheses,
  remaining budgets/patience, unresolved weaknesses and why further research is not needed
  before this submission. Do not close just because one candidate first became eligible,
  a planned batch ended, or enough report releases were published.

For example, an eligible strategy with validation Sharpe 0.95 and drawdown 29% cannot enter
test when the readiness targets require at least 1.0 and at most 25%. Lowering exposure may
fix drawdown while leaving return, Sharpe or trade support inadequate; check the entire vector.
Select among ready candidates using the predeclared ranking. If none is ready, follow the
diagnostic loop and research budget rather than opening test to see whether it rescues one.
Passing readiness does not guarantee a passing test, nor require using every remaining trial.

### One joint final evaluation

After submission readiness passes and before opening test, freeze a bundle containing the
selected factor library, factor directions and horizons, preprocessing/refit policy, one
strategy, engine versions, cost scenarios, data fingerprints, metric definitions and acceptance
rules. Bind the saved readiness results and closure decision for that exact candidate; a stale
check from another version cannot authorize test access. A permitted refit on all pre-test data
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

A failed or inconclusive test closes that frozen attempt with an unsuccessful outcome;
apply the lifecycle below to decide the next research action. Never overwrite its selected
strategy, engine, source snapshots or results. New research versions use separate artifacts.
Do not select a replacement winner on the consumed test. Renaming a study, resplitting seen
history, changing the seed or choosing a different endpoint does not restore independence.

### Research lifecycle and evidence for ending a run

Keep three facts separate: each frozen attempt's outcome, current research activity, and
whether the overall objective is satisfied. A runtime turn ending or a report being delivered
does not change a failed objective to passed. Use the shared plan/index and report to retain
these facts; the directory layout and implementation remain the agent's responsibility.

| Current evidence | Next action |
| --- | --- |
| A candidate fails eligibility or submission readiness; useful work and budget remain | Diagnose on train/validation and continue a bounded factor or strategy experiment. Repair invalid computations before interpreting returns. |
| A candidate passes readiness and search closure; test is genuinely unexposed | Freeze and perform the one predeclared evaluation. |
| Final attempt fails or is inconclusive; useful work and budget remain | Preserve the failed attempt and continue exploratory train/validation work on separately versioned candidates, including a return to factor discovery when warranted. Record post-test exposure; further confirmation requires unused evaluation data. |
| Useful research is finished but confirmation needs unavailable fresh observations | Record awaiting fresh evaluation data, overall objective unmet, and the exact data/protocol dependency. End the current execution without claiming success or repeatedly polling unavailable data. |
| An applicable research/runtime budget or the frozen patience rule is exhausted | Stop the affected search and record counters, objective gaps and next hypotheses. Finish permitted reporting/reproducibility work; keep the research outcome unsuccessful. |
| A prerequisite blocks all useful in-scope work, or the user stops the run | Preserve an explicit blocked/interrupted outcome and the concrete resume condition. |
| All final criteria and required deliverables pass | Record successful completion with their actual evidence. |

Research after test exposure is exploratory even when calculations use only train/validation:
the researcher has seen the failure. It must not silently retune on test, rerank test candidates,
or call that period unseen again. A future confirmatory attempt needs genuinely unused data,
an explicit new protocol and appropriate sample support. Do not automatically extend dates,
change instruments or reset counters to obtain it. A new version/session of the same study
must carry forward prior trials and known exposure; a version label cannot reset independence.

Keep total trial/submission counts and consecutive non-improvement rounds against the frozen
validation objective. A new factor name, stage switch, cosmetic edit or restart does not reset
patience. Document the measured improvement that resets it. Maximum budgets are ceilings,
not quotas; use readiness, measured convergence or a concrete blocker to justify stopping
search, rather than an arbitrary small batch count. Missing fresh test data alone is not a
reason to skip remaining useful research, and failed final performance alone is not a reason
to terminate the whole task. Preserve remaining budget and the exact stop reason in every
unsuccessful handoff; never describe exhausted confirmation data as successful completion.

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

When the study authorizes adjusted-price research, use the qualified proxy mode described in
[data-and-environments.md](data-and-environments.md). Apply consistent adjustment to OHLC,
disable separate action cash/share credits in that mode, and label the resulting simulations
as research proxies. Strict raw-price/consolidated-volume qualification remains separate.

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
