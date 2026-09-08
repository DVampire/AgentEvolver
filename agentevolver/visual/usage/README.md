# Shared Usage view

Run and Benchmark embed the same read-only component at `./api/usage`. The widget
uses local JavaScript, SVG and scoped CSS; no CDN, model request, account token or
frontend build is required. Both monitor deployers package `deployment_files()` so
existing gateway prefixes continue to work.

```javascript
import { mountUsage } from './usage.js';
const usage = mountUsage(container, { endpoint: './api/usage' });
usage.setFilters({ agent_name: 'website_builder_agent', metric: 'tokens' });
usage.setTokenSeries(['input_tokens', 'output_tokens', 'cache_tokens', 'total_tokens']);
console.log(usage.getTokenSeries());
// Dispose when removing the containing view.
usage.destroy();
```

A `[data-usage-endpoint="./api/usage"]` element mounts automatically. For an explicit
mount, omit that attribute. Instances keep their DOM, filters and refresh state
separate. Cost and token metrics switch between lines and bars. Charts
support per-record/time grouping and cumulative totals. Attribution is clickable;
the call table supports pagination, sorting, details and CSV export.

## Selecting token series

The Tokens tab initially displays four independently selectable series:

| Series | Meaning |
| --- | --- |
| Input (uncached) | Input excluding cache reads and cache writes |
| Output | Output tokens, including reported reasoning tokens |
| Cache | Cache read + cache write; unknown if either field is unreported |
| Total | Full input + output, counting cached input once |

`More token series` adds cache read, cache write, full input, unclassified input
and reasoning. Overview, Cache detail, All and None presets adjust visibility.
The mount option `initialTokenSeries` accepts the same field names as
`setTokenSeries`; an empty array intentionally starts with no series selected.
Selections are local to each widget and survive polling, filters and chart mode
changes. They do not alter summary cards, API totals, exported records or costs.
They are not saved across a full browser reload.

- **Lines:** one fixed color per dimension, a dashed Total reference, independent
  visibility and a Y scale based on the selected series. Focus/hover at one X
  position compares all selected fields, including missing-field coverage.
- **Grouped bars:** compare selected dimensions side by side; the bars are not
  added, since full input, total, cache and reasoning can overlap other fields.
- **Stacked components:** only selected disjoint components are stacked. If Cache
  is selected its read/write children become reference lines. Otherwise read and
  write can be stacked separately. Total, full input and reasoning always remain
  reference lines. Missing required components or conflicting input breakdowns
  suppress that stack; partial-coverage known subtotals carry a dashed cap.
- **Cumulative:** each dimension accumulates independently within the filtered
  range. Unknown current values remain gaps. Subsequent points are known subtotals,
  with field-level coverage rather than an invented zero for missing observations.
- **Incomplete attribution:** full input not explained by available components is
  shown as Unclassified input. The simple `Input + Output + Cache = Total` formula
  applies to a complete, consistent breakdown. Reasoning is never added again.

Every API series point includes `tokens`, `token_coverage`, `token_observations`
and `token_conflicts`. These are computed before any client-side visibility
selection. Time buckets and adjacent-record groups sum each dimension, including
cache, and retain its individual count of observed values.

The Python `UsageView(sources)` receives a callable returning trusted source
records: `id`, optional `log_root`, `task_id`, and `summary` (the benchmark spend
contract). Queries do not accept filesystem paths. `response(query_string)` returns
HTTP bytes and MIME type for `overview`, `call`, and `export` views. The overview
contains summary, series, breakdown, facets and paginated calls from one snapshot.

## Accounting and current coverage

- `trace/usage.py` incrementally projects complete JSONL records without importing
  providers or carrying prompt content into responses. Explicit event identities
  deduplicate copied traces. Truncation, file replacement and deletion rebuild the
  affected projection. The current cache is process-local and reconstructible.
- **Current records are legacy Agent steps**, not a complete physical HTTP request
  ledger. Model request events provide attribution and matched attempt counts.
  Matched starts are consumed per completion so a later resident turn reusing step
  zero does not inherit earlier request attempts.
- Step duration includes tool execution. The UI calls it *Step duration*; model-only
  latency and TTFT are not fabricated. Unfinished requests and retries without a
  completed step may have no recorded usage. Complete per-attempt terminal events
  in ModelContext remain a subsequent instrumentation change.
- Complete input already includes caches. Reasoning is not added to output again.
  The cache percentage is a ratio of token sums, including cache writes in input.
  Missing usage and explicitly reported zero usage remain distinct.
- Known cost distinguishes `reported`, `estimated`, and `legacy` source amounts.
  Unknown amounts stay unknown. No prices are looked up or recomputed in visual.
  Provider amounts and local estimates are not account invoices.
- Benchmark summaries are used when trace coverage is absent/incomplete; they are
  not added to overlapping trace rows and are never assigned fictional timestamps.
  Current task sessions and historical attempt paths are bound by the Benchmark
  view. Result scores and grading logic are not changed by this module.
- Time buckets sum consumption; large per-record charts group adjacent records
  rather than dropping them. Clicking a grouped point narrows the table's time
  range. Duration buckets show means, not a fabricated percentile. Full recorded
  rows remain available through pagination and export.

The older Benchmark `/api/status` telemetry contract remains for compatibility;
the embedded Usage component queries the shared endpoint directly. New dashboards
should use UsageView rather than copying that legacy telemetry reducer.

## Publishing existing monitors

Updating source files alone does not update previously deployed page copies.
Repackage/redeploy the monitor through DeploymentManager with its existing site ID
and state binding. This updates the view without launching an Agent or changing
benchmark results. Run's existing `deploy --state ...` command supports this.
