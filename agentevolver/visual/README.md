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
| `task/style.css`, `task/app.js` | Shared task visualization for semantic sections containing Markdown or authored HTML |
| `memory/style.css` | Memory visualization |
| `plan/style.css` | Plan visualization |
| `request/style.css`, `request/app.js` | Canonical LLM request viewer with context-layer, token-growth, cache, and compaction diagnostics |
| `benchmark/` | Generic live benchmark state, HTTP service, and responsive dashboard |
| `run/` | Generic Agent run dashboard |
| `usage/` | Shared real-trace usage charts, filters, call records and CSV export for Run and Benchmark |
| `sites/` | Unified page index |

Assets are grouped by the view they serve, not by file extension. Each view keeps
its stylesheet (`style.css`) and optional script (`app.js`) together. Python
renderers use `asset_path(view, filename)`, backed by PathManager. Do not recreate
the former top-level `css/` and `js/` buckets.

All task HTML uses a single `div.task` wrapper and the section tags `objective`,
`requirements`, `interface`, `acceptance`, `plan`, `deliverables`, `constraints` and
`notes`. Sections may repeat; use headings and stable IDs for subject-specific chapters.
Link `task/style.css` and `task/app.js` from the head using paths relative to the
document. The runtime task-page renderer uses the same two assets.

Sections contain Markdown text by default; escape literal HTML in code examples
(`&lt;h1&gt;`, for example). Use `data-format="html"` on a section containing authored
HTML to preserve headings, nested lists, tables and links. This uses the same layout
and section controls, with no separate page theme. Both forms are readable by the
task loader without executing JavaScript. Keep styling and scripts here, never inline
in task specifications. See [task authoring](../../examples/tasks/README.md).
The legacy `task/task.css` and `task/task.js` remain only for previously saved previews;
new task documents and generated pages do not select them.

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
