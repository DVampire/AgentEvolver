# Metrics and evaluation contract

Read this with [research-protocol.md](research-protocol.md) before implementing either
environment or inspecting candidate performance. It defines the numerical and diagnostic
contract; [reports.md](reports.md) defines its presentation. The supplied study owns dates,
budgets and acceptance thresholds. The conventions below fill unspecified definitions;
freeze them before search and never change a denominator, window or benchmark to rescue a
result. These instructions are a specification to implement and verify, not a built-in engine.

Sections: [shared contract](#one-contract-reproducible-results),
[factor definitions and diagnostics](#factor-identity-what-was-actually-mined),
[strategy rules and performance](#strategy-identity-what-is-being-traded),
[iteration decisions](#evaluation-drives-the-next-experiment),
[numerical checks](#numerical-acceptance-before-market-interpretation).

## One contract, reproducible results

Persist a versioned metric contract with IDs, formulas, units, valid input conditions,
sampling frequency, aggregation, null reasons and numerical tolerances. Both environments
export computed results using it; the report renders those results without independently
reimplementing financial formulas. Include the contract hash in result and cache identities.
Every official score binds the candidate/version, fitted state, data, engine, split/fold,
scored interval and cost scenario. Display rounding never determines a gate or a ranking.

Store fractional returns/coverage/drawdown as decimals (0.12 = 12%), correlations and
ratios as unitless values, costs as currency and/or explicitly named basis points
(1 bp = 0.0001), dates as exchange sessions, and durations as trading sessions. Distinguish
percent changes from percentage-point differences. JSON contains finite numbers or null,
never NaN/Infinity, a fabricated zero, or strings in numeric fields.

Every metric includes status and a reason when not measured: pending, blocked, error,
insufficient_sample, undefined or sealed. A measured value can separately pass or fail a
criterion. A valid zero is different from an unavailable value. Report the requested,
eligible, observed and actually scored counts, exclusions and date ranges. Missing required
market observations fail data readiness; they are not silent zero-return days.

Gate records contain criterion ID, metric ID/result ID, scope, value, operator, threshold,
verdict and explanation. Preserve every failed gate. Undefined required evidence yields
an inconclusive gate; any failed gate prevents passing, as does any inconclusive required gate.
An operational error or pending source check is not an evaluated market hypothesis.

## Factor identity: what was actually mined?

Keep every candidate/version, including rejected ones. A row records:

- ID, parent version, short name, economic hypothesis and falsification condition.
- Exact executable expression and readable formula; input fields/adjustment basis, units,
  lookback, warm-up, availability time and fitted transformations.
- Training-selected direction, primary prediction horizon, secondary diagnostic horizons,
  fitted-state IDs by fold and the predeclared refit policy.
- Trial/result IDs, actual execution state and eligibility decision with failed criteria.
- Consuming strategy IDs and exact factor versions; never bind a strategy to an unnamed
  latest factor. A proposed formula is not an evaluated or admitted factor.

Use separate execution states (proposed, running, evaluated, error, blocked) and selection
states (pending, admitted, rejected, inconclusive). Show both. An engineering fixture must
carry a synthetic scope and cannot appear in the real-data candidate leaderboard.

### Labels, samples and causality

For a daily factor x_t available after close t, the default h-session label enters at
open t+1 and exits at open t+1+h. Compute the total return of a unit investment using
the same split/dividend convention as the portfolio engine. Without actions it is
open[t+1+h] / open[t+1] - 1. This is a gross predictive label, not a net trading strategy.
Store both endpoints. Do not use close_t to open_t returns or let label endpoints cross
the scored fold boundary. Available past bars may warm up features without becoming scores.

Define eligible signal dates from the requested exchange calendar, lawful warm-up and
label-boundary exclusions, before looking at candidate values. Missing prices/features
inside that calendar remain missing in coverage counts. Report factor availability,
label availability and valid paired observations separately; never improve coverage by
dropping a candidate's failed values from the denominator.

Default factor coverage is finite factor values / eligible signal dates. Also report
paired coverage = finite factor-and-label pairs / eligible dates. A data-quality failure
cannot pass admission on factor coverage alone. Show each fold and pooled counts; pooled
coverage is the ratio of summed counts, not the average of differently sized folds.

### Factor metrics

| Metric ID | Definition and interpretation |
| --- | --- |
| `ic` | Pearson correlation of x_t and its h-session label across aligned dates within one stock/fold. Center both vectors and divide their dot product by their centered norms. |
| `rank_ic` | Pearson correlation of the average-tie ranks of x and its label (Spearman). Report raw and training-oriented values; oriented IC/RankIC uses the direction fitted on the allowed training window. Never take an absolute IC as evidence of a usable direction. |
| `primary_validation_rank_ic` | Equal-weight mean of the specified validation folds' oriented RankIC at the training-selected primary horizon/policy. Require finite evidence in every fold; no skipping a bad/undefined fold. Also show pooled RankIC, explicitly labelled as a different statistic. |
| `positive_validation_folds` | Number of specified folds with oriented primary RankIC strictly greater than zero, with the total expected fold count alongside it. A zero/undefined fold is not positive. |
| `nonoverlapping_label_count` | Within each fold and fixed horizon, start at the first eligible label, retain the next eligible interval whose entry is at or after the previous retained exit, and continue chronologically. Count valid retained pairs without shifting the anchor to improve results. Sum counts across folds; also show each fold. This is a sample-count diagnostic, not proof of independence or a computed effective sample size. |
| `rolling_rank_ic` | Same oriented correlation in a trailing window of signal dates, labelled by the latest label exit/availability date. Freeze window and minimum finite pairs before search (daily default: 126 eligible dates and 60 pairs); never span fold boundaries or plot it as information known at the earlier signal date. Null where support is insufficient. |
| `quantile_response` | Mean gross h-session label in each bin of training-fitted oriented factor cutpoints (default quintiles), plus median, pair count and uncertainty. Quantile labels run from low to high expected return. Keep duplicate cutpoints/empty bins visible; do not force ties into fake equal-size groups. |
| `top_minus_bottom_response` | Top-bin mean label minus bottom-bin mean label, in return percentage points. This describes conditional outcomes, not an executable long-short return, Sharpe or compounded PnL. |
| `max_abs_factor_correlation` | Maximum absolute Spearman correlation with another retained factor on aligned validation factor values, without future labels in the alignment rule. Report pair counts and the matrix. Freeze the correlation method if the study specifies another one. Undefined redundancy cannot certify complementarity. |

For IC, fewer than three finite pairs, constant inputs or a numerically degenerate denominator
yield null with a reason. The study's minimum evidence can be stricter. Preserve per-fold
direction and horizon if the predeclared policy refits them using expanding past-only data;
the primary score then evaluates that policy, not the best horizon selected after validation.
Report the same fixed diagnostic horizon grid for candidates; inspecting an extra horizon
to change selection consumes a new recorded validation decision within the study budget.

Apply the supplied admission rules to their exact scope: default primary mean RankIC,
positive-fold count, pooled coverage, total non-overlapping validation labels and pairwise
redundancy. Display minimum fold coverage/counts as diagnostics too. All folds need defined
primary correlations. Select complementary factors in a deterministic, predeclared order
and record which retained factor caused each redundancy rejection. Affine renaming of an
existing expression is not a distinct economic hypothesis. Additional diagnostic metrics
do not silently create or replace admission thresholds.

When admitting the first factor, the peer set is empty: the pairwise redundancy condition
is satisfied without a comparison, with that reason recorded and no invented correlation.
This differs from an existing peer whose correlation is undefined because observations
are missing or constant; the latter leaves complementarity inconclusive.

### Uncertainty and robustness

Report paired sample counts, non-overlapping counts, fold dispersion and training/validation
degradation before making a confidence claim. Overlapping labels are dependent. For confidence
intervals use a verified time-series block bootstrap on aligned observations, with block
length, seed, repetitions, confidence level and minimum support frozen in advance. Select
block length from training dependence/horizon information; make it at least the label horizon
and check sensitivity to longer blocks on research data. Insufficient blocks means unavailable
uncertainty. Independent-row resampling and default correlation p-values are not sufficient
for these overlapping financial labels. A percentile interval is not automatically a valid
null-hypothesis p-value or a multiple-testing correction.

Show performance by past-only-defined regime and each annual fold, including counts and
failed hypotheses. Freeze regime thresholds on training data. Track all tested formulas,
parameters, horizon/direction choices and validation submissions. Do not infer discovery
confidence from the best of many trials. Only publish adjusted significance, effective sample
size or Deflated Sharpe when the method and its inputs have been numerically verified.

## Strategy identity: what is being traded?

For every strategy/version show factor IDs and expressions, combination/weights, fitted
parameters, entry and exit conditions, target size, rebalance schedule, missing/neutral
signal behavior, regime/risk rules, next-open execution and costs. Supply readable pseudocode
that explains how a dated factor observation becomes a target, order, fill and realized PnL.
An opaque strategy name or factor-weight list alone is insufficient.

Include cash, matched buy-and-hold and executable single-factor baselines before combinations.
List all tried strategies, eligibility and reasons, not only the winning curve. Label the
validation-selected version independently from whether final test passed. Test contains only
the frozen strategy, selected factor diagnostics and predeclared benchmark/cost scenarios.

### Portfolio accounting and scope

Let E_0 be initial capital and E_t the reconciled marked equity after session t, with the
declared terminal liquidation included in the final value. Use cash plus shares times raw
mark prices, explicit actions, cash accrual, fees and adverse slippage. Daily r_t = E_t/E_(t-1)-1.
Zero-position days remain in the calendar with their actual cash return. An order/mark failure
does not shorten the scored interval. For Signal Foundry, K = 252 scored sessions/year and
cash/Sharpe reference rates come from study.json, rather than hidden engine defaults.

Those explicit action rules apply to verified raw-price accounting. When the study authorizes
the documented adjusted-price proxy, use consistently adjusted OHLC for fills and marks,
fractional proxy units, and zero additional split/dividend credits. Preserve original action
records for provenance only. Freeze this accounting mode in result/cache identities and
label its performance as proxy research, with strict data qualification reported separately.
The same return/risk formulas, chronology, costs and statistical gates still apply.

For a reference open p, slippage fraction s and commission fraction c, the default buy
fill is p*(1+s) and sell fill is p*(1-s). Commission is c*abs(shares_traded)*fill_price;
debit buys and credit sells after commission. Enforce affordability including fees and
freeze fractional-share/lot rounding. Slippage cost is abs(fill_price-p)*abs(shares_traded),
already embedded in cash accounting. Apply splits before that session's trading; dividend
entitlement and cash timing follow the explicit action convention. Predetermine the final
liquidation timestamp; a final close signal cannot cause a retroactive fill at that day's open.

Evaluate each train, validation fold and test scope with explicit initial holdings/capital,
first executable open and terminal mark/fill. The default fold replay starts flat and
liquidates at its declared end. Pooled validation metrics concatenate the non-overlapping
daily fold return vectors in order, including each fold's entry/liquidation costs, and
wealth-chain those returns from a common initial value. Do not concatenate absolute currency
equities from reset accounts, average fold Sharpes into pooled Sharpe, or include warm-up/gap
days as artificial zero returns. Label the pooled series as a fold-reset composite with its
gaps and scored-session annualization. Display the individual folds as well.

The gross comparison is a separate zero-commission/zero-slippage replay of the same frozen
signal and sizing policy. Its positions can differ when costs affect affordable sizing or
risk rules; identify this convention. Gross minus net terminal equity is not necessarily
the cash sum of fees. Export actual commissions and adverse slippage relative to reference
execution prices from the net ledger separately. Stress scenarios rerun the fixed policy
with the prescribed cost multiplier and no refitting.

### Strategy metrics

Here N is the number of scored daily returns, std uses sample ddof=1, rf_t is the daily
Sharpe reference, and d_t = r_t-rf_t. Convert a specified effective annual reference Rf to
(1+Rf)^(1/K)-1 per session. Use the same reference for all compared strategies and benchmarks.

| Metric ID | Formula / convention |
| --- | --- |
| `net_total_return` | E_N/E_0 - 1, equivalently product(1+r_t)-1. |
| `net_cagr` | (E_N/E_0)^(K/N)-1; scored-session annualization, explicitly labelled. It is not arithmetic mean return times K. |
| `annualized_volatility` | std(r, ddof=1) * sqrt(K). |
| `net_sharpe` | mean(d) / std(d, ddof=1) * sqrt(K). Report daily inputs/counts; conventional square-root annualization does not remove serial dependence. |
| `sortino` | sqrt(K) * mean(r-m) / sqrt(mean(min(r-m, 0)^2)), with daily minimum acceptable return m explicitly frozen (default: rf). The downside mean includes all N sessions, not just losing days. |
| `drawdown` / `max_drawdown` | D_t = E_t / max(E_0,...,E_t)-1. Plot D_t at or below zero; the gate uses positive max_drawdown = -min(D_t). |
| `calmar` | net_cagr / max_drawdown using the same scope and the positive loss denominator. |
| `recovery_sessions` | Sessions from an equity peak to the first return to that peak; also show peak-to-trough and trough-to-recovery. An ongoing drawdown is unrecovered with elapsed duration, not a fabricated recovery date. |
| `monthly_return` / `yearly_return` | product(1+r_t)-1 inside each calendar bucket. Label partial periods and fold boundaries; never sum daily percentages or stitch train/validation/test into one claimed OOS history. |
| `exposure` | Actual marked stock value / total marked equity. Report daily series, mean, maximum and invested-session fraction; distinguish actual weights from the previous-close decision target. |
| `turnover` | Daily sum of absolute reference-price traded notionals / equity immediately before that session's trading; show cumulative and K/N annualized turnover. Buying 100% then selling 100% counts about 2, not 1; use the actual changing equity denominator. Splits are not trades. |
| `commission_cost` / `slippage_cost` | Sum actual commission currency and adverse fill-versus-reference price difference times absolute shares, respectively. Show currency and basis points of initial capital, plus each period/order. Do not subtract slippage twice when it is embedded in fills. |
| `completed_round_trips` | Count flat-to-positive-to-flat inventory episodes. Adds/partial reductions are fills in the same episode; a still-open episode is not completed. Terminal liquidation closes an episode when required. |
| `win_rate` / `payoff_ratio` | Fraction of completed episodes with strictly positive net PnL (zero is not a win); mean positive PnL / absolute mean negative PnL, respectively. PnL includes allocated fees, slippage and attributable dividends. |
| `profit_factor` | Sum positive completed-episode net PnL / absolute sum negative completed-episode net PnL; not win rate or average payoff. |
| `active_return` / `information_ratio` | a_t = strategy net r_t - matched benchmark net r_t; show difference in total return separately in percentage points. Information ratio = sqrt(K)*mean(a)/std(a,ddof=1). |
| `sharpe_advantage` | Strategy net Sharpe minus matched buy-and-hold net Sharpe on exactly the same scored sessions. This is the study's comparison gate, not information ratio or CAGR outperformance. |
| `stressed_net_return` | Recomputed net_total_return under the study's frozen stress multiplier; use a rerun, not scaling the headline return. |

List completed-episode net PnL, holding sessions and entry/exit timestamps. Reconcile total
equity change with closed/open inventory PnL, dividends and cash accrual without double
counting; episode PnL definitions must agree with the position/cash ledger. Show the number
of fills separately from round trips, including final liquidation costs.

Fewer than two returns invalidates sample volatility/Sharpe. Zero dispersion/downside/risk
or no losing trades makes the corresponding ratio undefined (null with its denominator
reason), not a passing infinity. With no completed trades, trade ratios are null. Fewer
than the study's required sessions/trades fails that gate even when some metrics are finite.
Nonpositive equity is an insolvency/accounting outcome, not a silently omitted return.
Apply conservative null handling to all undefined formulas, including empty quantile bins.

For Signal Foundry, validation eligibility uses pooled net Sharpe, positive-return fold count
and pooled maximum drawdown. The joint final decision requires all seven study criteria:
net CAGR, net Sharpe, positive-loss maximum drawdown, completed round trips, scored sessions,
Sharpe advantage over buy-and-hold and stressed net total return. Load thresholds directly
from study.json. Extra attractive metrics cannot compensate for a failed criterion.
These preliminary validation gates are not submission approval. Apply
`acceptance.final_submission` as described in the [research protocol](research-protocol.md):
Signal Foundry also checks all seven final criteria on pooled validation before opening test.
Export that separate readiness vector with validation scope, exact values and reasons for
missing metrics. Readiness is never a test result and does not overwrite initial eligibility.

## Evaluation drives the next experiment

Before each market evaluation write a hypothesis, parent IDs, intended change, expected
metric movement, falsification condition, scope and remaining budget. Afterward save a
compact diagnosis with result IDs, baseline/candidate values and paired differences, gate
failures, uncertainty, cost and one explicit decision: repair, reject, retain, combine,
return to factor discovery, continue exploratory research, freeze or stop with a reason from
the research protocol. Link the full record from index.md. Report
errors separately from low performance; fix invalid accounting before interpreting returns.

| Observed training/validation evidence | Bounded next investigation |
| --- | --- |
| Low coverage, a constant factor, impossible IC or delayed data | Inspect inputs, formula warm-up, timestamps and label alignment; repair and rerun fixtures before another financial claim. |
| Training IC strong but validation IC weak or sign unstable | Inspect fold/regime and search breadth; reject overfit variants or test a simpler economic hypothesis. Never flip the sign on validation to relabel failure as success. |
| Positive IC but high redundancy | Compare exact expressions and aligned pair correlations; retain a simpler/stabler representative or propose an economically different input, not another name. |
| Predictive factor, weak net strategy | Trace signal → target → fill; compare executable single-factor baseline, turnover, costs and horizon versus holding duration. Investigate mapping/rebalance changes as new budgeted candidates. |
| Gross works, net/stress fails | Attribute fees/slippage and turnover; test a predeclared small change in rebalance cadence or entry hysteresis on research splits. Keep required costs unchanged. |
| High return but poor drawdown, fold stability or benchmark advantage | Diagnose actual exposure and losing intervals; try a bounded causal risk/regime hypothesis. A bull-market equity curve alone is insufficient. |
| Combination improves nothing over one factor | Ablate one factor at a time with the same dates, costs and refit policy; record both risk-adjusted and absolute-return changes and their paired uncertainty. |
| Too few trades or labels | Report inadequate support; reject or change the economic hypothesis on research data. Do not split resizes into fake trades or count overlapping labels as independent. |
| Eligible strategy misses submission targets or robustness checks | Keep test unexposed. Compare the whole readiness vector and continue a bounded hypothesis; reducing position size alone may not fix return, Sharpe, benchmark advantage or support. |
| Repeated validation stagnation or exhausted budget | Apply the study's frozen patience/budget rules with actual counters. Preserve unmet readiness/objectives; do not submit an unready candidate because search has stopped. |
| Failed or inconclusive final test | Preserve the frozen failure and continue useful, budgeted exploratory research on separate versions. Mark test exposure and the need for unused confirmation data. Apply the protocol's lifecycle to stop only for an evidenced limit/blocker, not failure alone. |

For parameter stability and ablations, predeclare small research-only comparisons, show every
variant and charge the appropriate trial/validation budget. Refit trainable parts only on
the permitted training data. Compare the same eligible dates and bootstrap paired return
differences if estimating uncertainty. Never introduce ablations as extra post-reveal test
selection opportunities. Do not optimize chart aesthetics as a substitute for research progress.

## Numerical acceptance before market interpretation

Verify these cases through the actual environment operations and exported report artifacts:

- Factor [1,2,3,4] and labels [0.01,0.02,0.03,0.04] give Pearson/Spearman 1;
  reversed order gives -1; a constant input gives null. Ties [1,1,2,3] rank as [1.5,1.5,3,4].
- A close-t signal with opens 100 at t+1 and 110 at t+2 has h=1 label +10% absent actions;
  changing prices after a feature's availability must not change that feature.
- Fold RankICs [0.10,-0.02,0.04] give primary mean 0.04 and two positive folds; one null
  fold makes the primary mean undefined, rather than averaging the other two.
- Equity [100,110,99] gives returns [0.10,-0.10], net total return -1%, drawdowns
  [0,0,-0.10] and positive maximum drawdown 10%. CAGR is 0.99^(252/2)-1 under K=252;
  its extreme short-sample annualization is not evidence of a reliable annual forecast.
- Net episode PnLs [20,-10,0] give three trips, win rate 1/3, payoff 2 and profit factor 2.
  Splitting the winning episode into three fills does not increase the trip count.
- A zero-position account at zero cash/reference rate has zero return and drawdown,
  zero trips, undefined Sharpe/Sortino/Calmar/trade ratios and cannot pass sample/trade gates.
- Reconcile split/dividend, next-open fills, partial resizes, both-side fees, adverse
  slippage and terminal liquidation against an independent hand-computable cash ledger.
- Check a null/failed gate, a threshold equality using the actual study operator, and
  displayed rounding near a threshold; only unrounded values drive acceptance.

Also test prefix/future perturbation, missing prices, split boundaries, identical-replay
cache hits, changed-definition cache invalidation and a final-result reread without another
test exposure. Synthetic successes verify engineering only. The report must reconcile a
chart point, metric row, gate and exported record for the same real result identity.

## Primary references

- [SciPy Spearman correlation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html): rank correlation, constant-input behavior and limitations of the default p-value.
- [William Sharpe, The Sharpe Ratio](https://web.stanford.edu/~wfsharpe/art/sr/sr.htm): differential returns, variability and interpretation across time periods.
- [arch time-series bootstraps](https://bashtage.github.io/arch/bootstrap/timeseries-bootstraps.html): stationary, circular and moving-block resampling for dependent observations.

Accounting, aggregation and display choices above are this method's declared conventions;
these sources do not establish profitability, independence or sufficient research evidence.
