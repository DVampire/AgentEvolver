---
name: visual
description: "This directory contains dependency-free browser renderers for AgentEvolver's HTML-native artifacts. Runtime parsers never execute these files; CSS and JavaScript are for human preview only."
version: 1.0.0
type: module
category: visual
requirements: []
metadata: {}
---
# Visual assets

This directory contains dependency-free browser renderers for AgentEvolver's HTML-native
artifacts. Runtime parsers never execute these files; CSS and JavaScript are for human
preview only.

| Asset | Purpose |
|---|---|
| `prompt/style.css`, `prompt/app.js` | Prompt HTML preview |
| `workflow/style.css`, `workflow/app.js` | Dynamic Workflow metadata and nested execution-program preview |
| `task/style.css`, `task/app.js` | Task visualization |
| `memory/style.css` | Memory visualization |
| `plan/style.css` | Plan visualization |
| `request/style.css`, `request/app.js` | Canonical LLM request viewer with context-layer, token-growth, cache, and compaction diagnostics |
| `benchmark/` | Generic live benchmark state, HTTP service, and responsive dashboard |
| `run/` | Generic Agent run dashboard |
| `sites/` | Unified page index |

Assets are grouped by the view they serve, not by file extension. Each view keeps
its stylesheet (`style.css`) and optional script (`app.js`) together. Python
renderers use `asset_path(view, filename)`, backed by PathManager. Do not recreate
the former top-level `css/` and `js/` buckets.

The views share the dashboard's deep-green/mint palette: `ground`, three surface
levels, text tiers, semantic green/amber/red/blue/purple accents, borders, and the
monospace stack. Layouts may differ because a prompt, plan, and long conversation
have different information density, but their color and type vocabulary must not.
`tests/test_request_viewer.py` checks the common token values to prevent a new view
from silently introducing a second theme, including drift from the benchmark palette.
The page index and run deployment cards show `deployed_at` (successful health check),
not `updated_at` (which also changes on stop). Legacy timestamps are shown as
"Not recorded", never inferred from status changes. Dates use the browser timezone.

Benchmark launchers publish a small `monitor.json` through `BenchmarkMonitor`; the
dashboard reads that state and the normal result ledger. Its process is started through
`deployment_manager`, so port allocation, health checks, persistence, and stopping use
the same lifecycle as every other deployed service.

The Workflow renderer reads the embedded `<workflow>` element without modifying it, so
the same complete HTML file remains valid input to `WorkflowCompiler` and a standalone
browser document.
