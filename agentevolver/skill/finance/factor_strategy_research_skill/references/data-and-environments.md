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
| [Alpaca market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) | SIP consolidates US exchanges; IEX is one venue. Historical SIP access and recency restrictions differ from latest-data permissions. Probe the actual account rather than inferring entitlement from a plan name. |
| [Alpha Vantage documentation](https://www.alphavantage.co/documentation/) | TIME_SERIES_DAILY_ADJUSTED provides raw OHLCV, adjusted close, splits and dividends. It is marked premium; compact data has only 100 observations. Verify full-history access. |

Use other documented providers if their licensed data meets the contract. The absence of
a configured provider is a feasibility problem to resolve with bounded probes, not a reason
to scrape arbitrary undocumented endpoints. An imported licensed CSV is a possible separate
study input, but cannot satisfy a task explicitly requiring a real connector download.

For the default consolidated-volume study, a single-exchange feed changes the hypothesis.
Do not silently splice feeds, mix adjusted close with raw open, or infer dividends from
price gaps. Record source revisions and historical-data/PIT limitations. A source that cannot
provide required actions, calendar or dates cannot pass the study's data acceptance.

## Connector contract

Build an MCP wrapper only when no adequate service exists. Follow self_evolving_skill's
connector templates; a bare requests script is not an AgentEvolver Connector. Design bounded
actions such as source description, coverage probe, stock bars, corporate actions and snapshot
export. The agent chooses final names from actual schemas.

Return compact structured metadata and artifact paths, not thousands of bars into prompt
context. Require explicit symbol, interval, feed, session and adjustment parameters. Preserve
raw response provenance, canonical schema, timestamps, completeness, request count and hashes.
Retry transient failures with bounds; respect rate limits and fail visibly on malformed or
partial data. Distinguish a cache hit from a fresh request. Make cache keys include every semantic
parameter and provider revision where available. Support a changed symbol/interval in reuse tests.

Normalize `timestamp, symbol, open, high, low, close, volume` with explicit UTC timestamps,
exchange-local session date, currency, provider/feed, raw/adjusted status and corporate-action
tables. Check positive prices, nonnegative volume, finite numeric fields, OHLC inequalities,
duplicates, sorted sessions and missing bars against the exchange calendar. Never fill a
missing trading price with a future price. Declarations of data quality do not replace checks.

## Two stateful environments

| Responsibility | Factor environment | Strategy environment |
| --- | --- | --- |
| Inputs | Versioned market snapshot, protocol, causal factor specification | Same snapshot/protocol, admitted factor-library version, strategy specification |
| State | Fitted transforms, fold definitions, factor trials, library and rejection reasons | Frozen factor bindings, orders/positions/cash, strategy trials and exposure ledger |
| Operations | Bind study, describe schema, evaluate training, validate bounded candidates, compare, admit, export report artifacts | Bind study/library, simulate training, validate, compare/ablate, freeze submission, finalize once, export ledgers/reports |
| Output | Factor IDs, coverage, IC diagnostics, eligible status, result paths | Net/gross returns, risk/cost metrics, acceptance results, trades and artifact paths |

These are interface requirements, not a fixed action-name list. Follow the real Environment
base class and action decorator. Inspect the current transport contract, serialize compact
action results explicitly (JSON text is portable) and return real failures. Include manifests,
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

Cache keys include snapshot, split, candidate, fitted state, engine and cost assumptions.
Use chronological slices and bounded batches; do not download data or rebuild the report
runtime for every formula. Results must carry schema/version information so a later engine
change cannot silently reuse stale metrics. Test this invalidation explicitly.

## Engineering evidence before financial claims

Use hand-computable fixtures for a next-open fill, zero signal/cash, buy-and-hold, split,
dividend, fee on entry/exit, terminal liquidation and a gap in required prices. Compare an
independent small accounting reference with the candidate; two code paths sharing the same
bug are weak evidence. Add prefix/future-perturbation tests for features and fitted parameters,
as well as changed-candidate-after-freeze, consumed-test-after-crash and cache-invalidation cases.
Synthetic fixtures and an independent calculation are self-produced engineering checks,
not independent market validation or profitable trading evidence.
