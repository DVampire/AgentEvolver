# Sources and capability interfaces

## Discover an authorized source

Verify current provider documentation and actual account entitlements before choosing.
Use environment-held secrets through the connector; never print them, put them in a task,
serialize them into a report or buy a subscription. Do not assume a public URL grants
redistribution rights. Preserve permitted metadata; publish raw data only if allowed.

These official sources are starting points, checked on 2026-09-12:

| Source | Relevant contract and feasibility check |
| --- | --- |
| [Alpaca historical stock bars](https://docs.alpaca.markets/us/reference/stockbars) | Multi-symbol bars, pagination via next_page_token, explicit feed and adjustment options. Freeze every query parameter and follow all pages. |
| [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) | SIP consolidates US exchanges; IEX is one venue. Historical SIP queries ending at least 15 minutes ago can be available without a paid subscription, but still require valid credentials. Verify account access and session semantics; a 401 alone does not establish a paid-plan requirement. |
| [Alpha Vantage documentation](https://www.alphavantage.co/documentation/) | TIME_SERIES_DAILY_ADJUSTED provides raw OHLCV, adjusted close, splits and dividends. It is marked premium; compact data has only 100 observations. Verify full-history access. |
| [Tiingo EOD documentation](https://www.tiingo.com/documentation/end-of-day) | Token-authenticated historical date-range queries expose raw and adjusted fields plus split/dividend information. Verify account limits, volume/session coverage and rights against the study before selecting it. |
| [yfinance](https://github.com/ranaroussi/yfinance) and its [download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) | A public-data client that can be wrapped by a local MCP server. Check use rights, actual availability, adjustment semantics and volume coverage; it is not an official Yahoo SDK or a guaranteed study-ready source. Set date range and adjustment/action flags explicitly. |

These are candidates, not a provider allowlist or a requirement to obtain commercial keys.
Use other documented providers or maintained public-data clients if their data meets the
contract and use rights. Check at least a plausible alternative before treating one provider's
authentication failure as a study-wide blocker. Absence of an API key does not exclude a
public download. An imported licensed CSV is a possible separate
study input, but cannot satisfy a task explicitly requiring a real connector download.

For a study requiring strictly consolidated volume, a single-exchange feed changes the hypothesis.
Do not silently splice feeds, mix adjusted close with raw open, or infer dividends from
price gaps. Record source revisions and historical-data/PIT limitations. A source that cannot
provide strict fields cannot pass strict qualification. Apply the explicit research policy
below separately; required session coverage and valid OHLCV still need to pass.
In particular, disabling a client's auto-adjustment does not prove that provider OHLC or
volume is in as-traded units. Verify the underlying adjustment basis and corporate actions;
report a specific unmet contract field rather than declaring every public source invalid.

### Public-data research policy

Read data_policy from the study before deciding a source is blocking. Signal Foundry v2
explicitly authorizes public-data research with separately reported strict qualification.
Provider-native volume with uncertain consolidated coverage and documented adjusted prices
may support that qualified research. Do not require a commercial feed or raw-price certificate
before any factor or strategy calculation when the user has authorized this mode.

Retain the original provider OHLCV, adjusted close, actions and source/version metadata.
For an adjusted-price proxy, validate adjusted_close/close as finite and positive and apply
that ratio consistently to all four OHLC fields. Verify OHLC inequalities and session coverage
after normalization. Execute the simulation on fractional proxy units, with no extra split
share changes or dividend cash credits; otherwise actions are counted twice. Retain action
records for audit. State provider-volume units and uncertainty in volume-based hypotheses.
Never label these proxy prices as raw, SIP-certified or historically executable fills.
Do not mix raw opens with adjusted closes. Test both raw/action accounting and adjusted-proxy
accounting as distinct modes with different cache identities. Treat gaps, malformed prices,
inadequate coverage and unknown adjustment ratios as actual data defects to repair.

This proxy convention follows the adjustment-ratio method in the maintained
[yfinance implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/utils.py),
not an assertion that the upstream data is certified as-traded history. Preserve the client
version or inspected source revision and the exact normalization method in the snapshot.

The report presents research performance and strict data qualification separately. All numeric
gates are still calculated on the declared research basis with unchanged dates/costs/thresholds.
Passing those gates does not turn unmet strict-source requirements into a pass. If the study
does not authorize a fallback, keep its original requirements and request a scope/source change.

## Recovering data access

Check credential presence in the actual connector process without exposing values. A key in
the launcher's shell is not evidence that a container or MCP subprocess received it. Record
missing configuration, invalid authentication, insufficient entitlement and unavailable
coverage separately. Retry transient failures with bounds; do not retry unchanged 401/403
requests or a documented demo-key restriction as if they were transient. Investigate another
documented source within an explicit small feasibility budget, keeping the study unchanged.

If no usable source is available, write the source receipt and the exact configuration or
access change needed in the plan. Request that prerequisite through the available user-facing
channel, without requesting secret values in a report or conversation. Do not invent repeated
factor/strategy trials against the same missing snapshot: record proposals as pending and one
representative prerequisite check. Research budgets count evaluations, not renamed blocked
proposals; retain all attempted calls in the operational record. Failed numerical evaluations
still consume their trial allowance. Preserve any attempts already charged by the existing
ledger rather than reclaiming budget on resume.

Implement the source-independent numerical engines while data access is being resolved,
within the remaining budget. Their missing implementation is not an external prerequisite.
Use the same numerical implementation and research interfaces on
isolated, explicitly synthetic fixtures; keep those states out of real factor admission and
financial results. If meaningful independent work is exhausted, stop with a concise blocked
handoff and any existing usable report. Release counts and evolution coverage may remain
unmet. An elaborate substitute dashboard, dummy trials or additional failure-only components
do not resolve an access prerequisite.

On resume, inspect the prior plan, source configuration and generated implementations before
launching another full run. A new credential does not complete missing engine operations.
Repeat the bounded acquisition check after the prerequisite changes, then implement and
verify the remaining successful paths without resetting exposure or experiment history.

## Connector contract

Follow self_evolving_skill's Connector reference for local MCP authoring, registration and
repair, exactly as for other generated component types. The market-data methods belong here;
the component lifecycle belongs to that shared skill. Design bounded
actions such as source description, coverage probe, stock bars, corporate actions and snapshot
export. The agent chooses final names from actual schemas.

Prefer read-only remote retrieval returning a JSON object (bars/actions/provenance), with
permission_mode: read_only and result_mode: artifact in CONNECTOR.md. The server must actually
perform reads only: no cache files, arbitrary output_dir, remote changes or hidden writes.
The framework persists the complete response in its session connector logs and returns
artifact_path, sha256, bytes and result_key. Normalize the JSON artifact's result field into
workspace snapshots using Bash. This preserves native acquisition evidence without dumping
bar arrays into context or falsely labelling a file-writing MCP method read-only. The common
self_evolving_skill Connector reference owns this reusable transport pattern.

Return compact structured metadata and artifact paths, not thousands of bars into prompt
context. Require explicit symbol, interval, feed, session and adjustment parameters. Preserve
raw response provenance, canonical schema, timestamps, completeness, request count and hashes.
Retry transient failures with bounds; respect rate limits and fail visibly on malformed or
partial data. Distinguish a cache hit from a fresh request. Make cache keys include every semantic
parameter and provider revision where available. Support a changed symbol/interval in reuse tests.
Use MCP execution errors for failed downloads or invalid requests, so the runtime receives a
failed call. Returning an ordinary JSON string with `ok:false` is not an MCP error. Source
inspection can successfully report missing credentials, but that is not a successful download.

Normalize `timestamp, date, symbol, open, high, low, close, volume`: `timestamp` is UTC and
`date` is the exchange-local YYYY-MM-DD session date (map a provider's `session_date` here).
Keep currency, provider/feed, raw/adjusted status and corporate-action
tables. Check positive prices, nonnegative volume, finite numeric fields, OHLC inequalities,
duplicates, sorted sessions and missing bars against the exchange calendar. Never fill a
missing trading price with a future price. Declarations of data quality do not replace checks.

Reopen the saved native response before accepting acquisition. Compare its SHA-256 with the
receipt; normalize into a workspace snapshot and reopen that file too. Keep both hashes and
paths in the plan's data receipt. Check that every required field exists, the requested
symbol/interval matches, counts are nonzero, numeric values are finite and the expected
exchange sessions match exactly. Calendar dates may be holidays: construct the calendar with
padding around request bounds, then select sessions inside the requested interval. Do not
treat January 1 or a weekend endpoint as an invalid request or missing trading session.
Exercise a changed interval, holiday bounds, empty/partial responses and disk-read failures.
For this study, request only train/validation during research; download test after freeze.

Run the bundled structural checker against the actual saved artifact. It never downloads
data or grants source qualification. Dependencies: exchange_calendars (which supplies pandas).
Adapt the bars to the canonical fields above or pass their JSON pointer:

```bash
python {skill_dir}/scripts/check_snapshot.py /absolute/saved-response.json \
  --sha256 RECEIPT_SHA256 --symbol NVDA --start 2016-01-01 --end 2023-12-31 \
  --calendar XNAS --bars-pointer /result/bars --adjusted-close adjusted_close
```

Use the current study's symbol/dates, not these example values. `--adjusted-close` names the
retained provider field and checks that its ratio is valid; it does not normalize prices or
certify action semantics. Save the check's JSON receipt. A nonzero exit means acquisition
acceptance is unfinished. Re-run on any changed snapshot. Preserve provider timestamps,
currency, query/version and actions alongside the bars and inspect their semantics separately.

## Two stateful environments

Read [metrics-and-evaluation.md](metrics-and-evaluation.md) before implementing numerical
operations. Both environments bind the same versioned metric contract, export definitions,
candidate/result identities, per-fold and aggregate values, series, counts, gate verdicts and
explicit null reasons. Their artifacts feed the continuous report described in
[reports.md](reports.md); factor and strategy outputs do not require separate website routes.

| Responsibility | Factor environment | Strategy environment |
| --- | --- | --- |
| Inputs | Versioned market snapshot, protocol, causal factor specification | Same snapshot/protocol, admitted factor-library version, strategy specification |
| State | Fitted transforms, fold definitions, factor trials, library and rejection reasons | Frozen factor bindings, orders/positions/cash, strategy trials and exposure ledger |
| Operations | Bind study, describe schema, evaluate training, validate bounded candidates, compare, admit, export report artifacts | Bind study/library, simulate training, validate, compare/ablate, freeze submission, finalize once, export ledgers/reports |
| Output | Exact formulas/fitted versions, coverage, IC/RankIC, fold/horizon/quantile diagnostics, admission/rejection evidence and result paths | Exact trading rules/factor bindings, net/gross/benchmark series, risk/cost metrics, gates, trades/ablations and artifact paths |

These are interface requirements, not a fixed action-name list. Follow the real Environment
base class and action decorator. Inspect the current transport contract and return compact
results as a Response or mapping with explicit success/message/data. A JSON string containing
an error does not mark the native call failed. Include manifests,
session-scoped state keyed by context and cleanup. Do not assume a registered environment is
in env_names: the demo permits evolved Environment actions through the shared capability router.

Bind immutable snapshot, protocol and engine versions before evaluating. For deferred test
acquisition, freeze the query contract first and bind the downloaded hash in a one-time receipt
after submission freeze, before computing metrics; never fabricate an unavailable hash.
Finalization accepts
one frozen bundle covering BOTH environments and the factor library. The factor environment
must not have an independent early test-reveal action. Store trial IDs, validation counts and
the test-attempt marker durably before evaluation; concurrent or retried calls must not reset
them. Same submission may read the cached final result; changed submissions are refused.

Cache keys include snapshot, split, candidate, fitted state, engine, metric contract and cost assumptions.
Use chronological slices and bounded batches; do not download data or rebuild the report
runtime for every formula. Results must carry schema/version information so a later engine
change cannot silently reuse stale metrics. Test this invalidation explicitly.

## Engineering evidence before financial claims

Before calling either environment implemented, exercise a successful path through the same
interfaces and numerical code that will consume market data. A separate `run_fixture` demo
cannot validate a research action that unconditionally returns blocked or null metrics.
Use isolated fixture studies to check factor values, labels, fitted transforms, diagnostics
and admission decisions; then strategy signals, next-open orders, cash/shares, costs and
result metrics. Also exercise validation, joint freeze and finalization state transitions.
Fixtures may simulate eligibility within their own test state but never enter the real study's
eligible library or consume its test attempt. Exported artifacts must contain computed results
on valid inputs and explicit errors on invalid ones. Track missing operations individually;
registration and rejection-path tests are not full engine readiness.

Use hand-computable fixtures for a next-open fill, zero signal/cash, buy-and-hold, split,
dividend, fee on entry/exit, terminal liquidation and a gap in required prices. Compare an
independent small accounting reference with the candidate; two code paths sharing the same
bug are weak evidence. Add prefix/future-perturbation tests for features and fitted parameters,
as well as changed-candidate-after-freeze, consumed-test-after-crash and cache-invalidation cases.
Synthetic fixtures and an independent calculation are self-produced engineering checks,
not independent market validation or profitable trading evidence.
