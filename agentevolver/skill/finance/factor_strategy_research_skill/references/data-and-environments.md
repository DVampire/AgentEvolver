# Sources and capability interfaces

Sections: [source discovery](#discover-an-authorized-source),
[access recovery](#recovering-data-access), [Connector and local files](#connector-contract),
[two research Environments](#two-stateful-environments),
[engineering verification](#engineering-evidence-before-financial-claims).
Research chronology and stopping judgments live in the [workflow](research-workflow.md).

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

Read data_policy from the study before deciding a source is blocking. Signal Foundry
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
remain in the trial ledger, even when repaired or rejected. Do not erase retries or
validation looks from this experiment's search history.

Implement the source-independent numerical engines while data access is being resolved,
within the remaining budget. Their missing implementation is not an external prerequisite.
Use the same numerical implementation and research interfaces on
isolated, explicitly synthetic fixtures; keep those states out of real factor admission and
financial results. If meaningful independent work is exhausted, stop with a concise blocked
handoff and any existing usable report. Release counts and evolution coverage may remain
unmet. An elaborate substitute dashboard, dummy trials or additional failure-only components
do not resolve an access prerequisite.

When a prerequisite changes during this experiment, inspect its current plan, source
configuration and implementations. A new credential does not complete missing engine
operations. Repeat the bounded acquisition check, then implement and verify the remaining
successful paths without resetting this experiment's exposure or history. A separate
experiment starts from its supplied task and creates its own records and implementations.

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

Reuse `expected_sessions(start, end, calendar)` from `scripts/check_snapshot.py` in generated
engines (copy and pin this script with the component). It returns exchange-local ISO dates
and handles holiday/weekend bounds. Compare the saved dates to that exact sequence; never
clip the requested interval to the available bars. Run a valid research-mode calendar case
as well as a missing-session rejection: a fixture mode that skips calendar checks proves
neither, and an unexpected DateOutOfBounds is an implementation failure, not a passing test.

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
Keep factor production/fitting and strategy target generation extensible: the pilot's
percentile transform, threshold, sizing helper or holding horizon is one candidate policy,
not an engine-wide rule. Bind candidate-specific fitting/state and prospective objective
identities in results/cache keys so different combinations run through the same accounting.

| Responsibility | Factor environment | Strategy environment |
| --- | --- | --- |
| Inputs | Versioned market snapshot, protocol, open causal factor specification and declared role | Same snapshot/protocol, exact strategy-specific factor bindings/roles, qualification receipts and open strategy specification |
| State | Fitted transforms, fold definitions, factor trials, library and rejection reasons | Frozen factor bindings, orders/positions/cash, strategy trials and exposure ledger |
| Operations | Bind study, describe schema, evaluate training, validate bounded candidates, compare, admit, export report artifacts | Bind study/library, simulate training, validate, compare/ablate, freeze submission, finalize once, export ledgers/reports |
| Output | Exact formulas/fitted versions/parents, role-specific metrics, coverage and fold/conditional diagnostics, scoped admission evidence and result paths | Exact trading rules/factor bindings/parents, net/gross/benchmark series, risk/cost metrics, gates, paired comparisons and artifact paths |

These are interface requirements, not a fixed action-name list. Follow the real Environment
base class and action decorator. Inspect the current transport contract and return compact
results as a Response or mapping with explicit success/message/data. A JSON string containing
an error does not mark the native call failed. Include manifests and use call-scoped trial
instances with durable artifacts as described below. Do not assume a registered environment is
in env_names: the demo permits evolved Environment actions through the shared capability router.

Bind immutable snapshot, protocol and engine versions before evaluating. For deferred test
acquisition, freeze the query contract first and bind the downloaded hash in a one-time receipt
after submission freeze, before computing metrics; never fabricate an unavailable hash.
Normalize the effective warm-up, horizon, gap and cost settings at binding; later research,
freeze and finalization must use those same values. A missing optional warm-up key must not
crash only at freeze, and a fallback must not silently change the scored interval. Expose the
legal next action and its input artifact paths. Finalization without its required access
receipt must fail before reading data or changing exposure; it must name the prerequisite
operation instead of returning only a missing internal filename.
Finalization accepts
one frozen bundle covering BOTH environments and the factor library. The factor environment
must not have an independent early test-reveal action. Store trial IDs, validation counts and
the test-attempt marker durably before evaluation; concurrent or retried calls must not reset
them. Same submission may read the cached final result; changed submissions are refused.

Cache keys include snapshot, split, candidate, fitted state, engine, metric contract and cost assumptions.
Include canonical implementation hashes, role/target definitions and exact factor bindings;
names alone are insufficient cache identities. Qualification receipts are separate immutable
evidence bound to compared versions; adding a receipt must not mutate their implementations.
Use chronological slices and bounded batches; do not download data or rebuild the report
runtime for every formula. Results must carry schema/version information so a later engine
change cannot silently reuse stale metrics. Test this invalidation explicitly.

### Batch execution and result summaries

Both generated Environments must implement configurable bounded parallel evaluation for the
joint-candidate workflow, within batches and across independent factor/strategy work. Accept a saved
batch manifest with round ID, snapshot/protocol identities, strategy definitions and their
exact factor dependencies. Action names remain chosen by the generated environments. Factor
calculation and strategy simulation stay separate responsibilities within one round; the
strategy environment consumes the factor environment's pinned artifacts. Reuse the compiler's
multi-factor DataFrame output and identical fold/features/benchmark calculations rather than
one download, compilation or model exchange per formula. Respect memory/time limits; chunk
large batches and return progress paths through job when needed.

The dependency manifest is many-to-many: compute each shared factor once per exact
definition/data/fold/fit identity, then let ready strategies read their required columns
concurrently without mutating the shared frame. Different fitted transforms need distinct
cache entries. Read variable-length factor bindings, including five or more inputs; keep
weights, policy state and consumer contribution results local to each strategy version.

Separate feature readiness from diagnostic completion and admission. Publish an immutable
factor-value artifact with data/code/fit hashes, time index, columns and availability checks
through a separate completed materialization action. Factor diagnostic evaluation and exploratory strategy simulation
can then consume that artifact concurrently; strategy simulation must not require completed
factor scores or admission receipts. Wait only for actual inputs, including training-fitted
directions/transforms when a policy uses them. A pending diagnostic is not a missing feature.
Do not require a `factor-result` with `status=evaluated` just to run a research strategy;
validate the feature/fit manifest instead, and join diagnostic receipts for qualification.
Writing features early inside an action that retains their directory's write claim still
blocks readers. Check the real Manager dependency boundary, not only file existence.

Schedule ready strategies and independent factor computations concurrently with bounded workers;
do not impose a whole-batch barrier when only a strategy's own inputs are needed. Join completed
diagnostics, backtests and contribution comparisons for qualification, pool selection and
report export. Missing evidence remains pending, never implicitly passed.

Use the framework's independent-evaluation contract from the self-evolving Environment
reference. Prefer **one native action per trial**, `state_scope="call"`, and the same
study-scoped `concurrency_group`/`max_concurrency` across both engines. Each call reconstructs
its binding from a saved immutable study/snapshot specification. Manager/Runtime supplies a
fresh instance, admission, cancellation and cleanup; generated business code needs no runtime
imports, owner maps, semaphores or worker registration. Agent batch admission follows the
same declaration, without a duplicate `parallel_safe` flag. Submit ready independent Manager
calls together; `max_actions` is not a worker limit.

Declare `read_paths` for input artifact arguments and `write_paths` for the trial output
directory. Inputs may be shared; every simulation owns its fitted state, arrays, positions,
cash, RNG and output directory. Avoid global seeds, mutable cached DataFrames, process-wide
`chdir` and a shared `current_trial`. Publish fitted/features artifacts atomically before
consumers run. Keep status/receipts durable because call-scoped instances do not persist.

Write async methods for async I/O, or synchronous evaluation methods for Manager thread
offload. Threads keep the dispatcher responsive but do not speed up GIL-bound Python or
support forced termination. Use owned processes for those workloads, with explicit join and
cleanup; do not hide a new full-size pool inside each trial. Account for numerical-library
threads in the shared CPU/memory allowance. For unusually long work, use existing job
facilities and return status/result paths; status must not resubmit a completed trial.

Final-test access/exposure still requires a single durable atomic ledger across both engines.
Use a database transaction or declare the shared ledger argument in `write_paths` for all
access-changing operations. Call isolation is not a substitute for this protocol. Unchanged
submissions can return a cached result; changed submissions cannot acquire extra attempts.
Exploratory calls must not write that ledger. Short status operations may use
`capacity_exempt=True`; it does not bypass path claims. Use the common Environment guidance
for completed results versus live job status; do not poll a directory held by a worker and
claim the observer is nonblocking.

Verify within-engine and cross-engine overlap against uncached serial results, including
partial failure, duplicate identities, cancellation and owner exit. Cancelling one trial
must preserve unrelated successful results. Performance claims require measured worker
intervals; declaring async methods alone is not evidence.

Persist each trial start before execution, then its terminal status, semantic cache key,
result ID/path/hash and actionable error. Successful candidates survive a partially failing
batch; return counts and per-item statuses, explicitly marking partial failure. Never turn
an error into a zero score, abort unrelated candidates or replay the entire successful batch
after one failure. A factor failure blocks its dependent strategies, not other routes.
Concurrent writers must serialize ledger/catalog changes and claim the same evaluation key
once; incomplete results are not cache hits. Lock only shared publication/metadata updates,
not the whole numerical computation. A dependent consumer waits for its required artifact,
not unrelated diagnostics. Retries preserve both the original attempt and its recovery.

Return compact summary metadata plus catalog, batch-results and per-evaluation paths, not
all price arrays or full tables into context. Use the workflow's
[directory/version conventions](research-workflow.md#directory-and-version-conventions).
Before strategy evaluation, apply the workflow's [strategy definition archive](research-workflow.md#strategy-definition-archive)
and run its checker with --require-implementation. Package/pin that validator with the
generated environment if imported as a helper. Recheck code hashes before use, verify parent
IDs against the catalog and factor IDs/roles against actual factor artifacts, then execute
the named entrypoint through the native interface. Embed the unchanged strategy_spec and
its canonical spec_sha256 in the result; the checker alone does not execute the policy.
Use schema 2 definitions for new research. Only research_role=candidate with at least two
distinct, executed factor inputs can enter the working pool or final submission. Propagate
research_role/control_for from definitions into results, never default every policy to a
candidate. Resolve ablations to exact full candidates; benchmarks and ablations remain
executable diagnostics. Check actual factor dependencies and redundancy/contribution evidence:
zero-weight, unused or duplicated inputs do not prove a multi-factor strategy. Report executed
input IDs and contribution-check result IDs; fail candidate eligibility if these are missing.
Each result exposes schema version, candidate/version/parents, exact factor bindings, data,
engine/metric/fitted identities, fold scope, measured metrics, failed criteria and series
paths. Export common summary fields for every candidate so the Agent can compare a batch
from JSON without guessing field names. At the pilot, verify the actual saved JSON against
the declared schema, including list/object shapes, nulls and invalid/empty results. Build one
reader/adapter for that schema and reuse it for batch summaries and reports; a report-schema
field name is not proof that the numerical engine uses the same name. Contract violations
need an explicit error with its artifact path, not an empty-dictionary or zero-score fallback.
Keep selection/pool membership separate from numerical evaluation. Cheap batch screening
can precede expensive contribution/uncertainty checks; export the latter as pending, never
passed or zero. Complete the exact candidate's required evidence before final eligibility.

Before scaling, exercise both native interfaces with independent jobs and record worker start/end
times proving actual computation overlaps, not just queued submissions. Compare the same uncached
workload serially and concurrently with identical inputs, fit identities and per-trial seeds;
check result parity within declared numerical tolerances and report wall time, worker limits and
measured speedup (including a slowdown). Do not claim speedup from cache hits or concurrency labels.
Also check mixed ready/pending/failed dependencies, atomic publication, duplicate-key claims,
exact-retry cache hits, changed-definition invalidation and partial-batch recovery on small fixtures.
Keep these receipts linked from the plan index; use measurements to size subsequent batches. This interface is
a reusable capability to author and verify through self_evolving_skill, not a built-in
financial engine or a fixed menu of strategies.

### Open definitions and joint research

Implement a versioned callable/specification boundary for factors and policies. The agent
uses the skill's [expression compiler](factor-expressions.md) for factor definitions and
loads its generated `compute_factors(DataFrame) -> DataFrame` plus pinned runtime. Strategy
representations remain open. Additional factor operations require versioned extensions and
verification rather than handwritten replacements for already supported expressions.
Do not restrict research to initial formula IDs or a
fixed menu of threshold modes. New mechanisms can require implementation work; use the shared
self-evolution method to improve the environment when its interface blocks valid research.
Unknown definitions/modes must fail explicitly, never fall back to median/average rules.

Preserve parent versions and snapshots. Support factor revisions, policy revisions, different
factor sets per strategy and aligned before/after exports. An exploratory consumer operation
may evaluate a not-yet-qualified role for admission evidence; it must be labelled research-only
and cannot pass strategy eligibility or finalization before scoped qualification. Evaluate
predictive redundancy among co-consumed factors when the study specifies that scope, keeping
replacement alternatives and all historical results available.

Export route/family identities, attempted mechanism coverage, role and consumer admission,
paired factor/strategy revisions and route-review evidence for submission readiness. Share
complete trial and validation-look records across both responsibilities. Support adjustable
batches, changed definitions and repeated rounds. Engines calculate and export evidence;
they do not decide whether research must continue. The Agent applies the workflow's
[completion decision](research-workflow.md#completion-decision), including reasoned stagnation.

## Engineering evidence before financial claims

Before declaring an operation ready, exercise a successful path through the same
interfaces and numerical code that will consume market data. A separate `run_fixture` demo
cannot validate a research action that unconditionally returns blocked or null metrics.
Use isolated fixture studies to check factor values, labels, fitted transforms, diagnostics
and admission decisions; then strategy signals, next-open orders, cash/shares, costs and
result metrics. These checks and a real planned multi-factor pilot precede batch research.
Include a successful comparison of two saved evaluations before claiming comparison support,
with real string candidate/fold IDs and multiple fold boundaries. Check replay/export modes
with saved outputs too; success of evaluate does not execute those branches.
Implement and exercise joint freeze/finalization state transitions before enabling final-test
access; they need not delay the initial research batch. Deferred operations must return explicit
not-ready errors without accessing test data, and must not be claimed as verified capabilities.
Fixtures may simulate eligibility within their own test state but never enter the real study's
eligible library or consume its test attempt. Exported artifacts must contain computed results
on valid inputs and explicit errors on invalid ones. Track missing operations individually;
registration and rejection-path tests are not full engine readiness.

Demonstrate a newly authored factor outside the initial inventory and a different policy
implementation through the actual native interface on training/fixture data. Then revise a
factor, compare its consumer against the unchanged parent, and verify a second strategy can
keep its original bindings. Check unknown definitions, stale cache identities, role mismatch,
and attempts to admit a supporting factor without a consumer comparison. These checks must
exercise authored numerical definitions, not produce canned metrics for new names.

Use hand-computable fixtures for a next-open fill, zero signal/cash, buy-and-hold, split,
dividend, fee on entry/exit, terminal liquidation and a gap in required prices. Compare an
independent small accounting reference with the candidate; two code paths sharing the same
bug are weak evidence. Add prefix/future-perturbation tests for features and fitted parameters,
as well as changed-candidate-after-freeze, consumed-test-after-crash and cache-invalidation cases.
Synthetic fixtures and an independent calculation are self-produced engineering checks,
not independent market validation or profitable trading evidence.
