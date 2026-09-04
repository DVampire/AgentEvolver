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
| `css/prompt.css`, `js/prompt.js` | Prompt HTML preview |
| `css/workflow.css`, `js/workflow.js` | Dynamic Workflow metadata and nested execution-program preview |
| `css/task.css`, `js/task.js` | Task visualization |
| `css/memory.css` | Memory visualization |
| `css/plan.css` | Plan visualization |
| `css/request.css`, `js/request.js` | Canonical LLM request viewer with context-layer, token-growth, cache, and compaction diagnostics |
| `benchmark/` | Generic live benchmark state, HTTP service, and responsive dashboard |

The views share one dark terminal palette: `ground`, three surface
levels, text tiers, semantic green/amber/red/blue/purple accents, borders, and the
monospace stack. Layouts may differ because a prompt, plan, and long conversation
have different information density, but their color and type vocabulary must not.
`tests/test_request_viewer.py` checks the common token values to prevent a new view
from silently introducing a second theme.

Benchmark launchers publish a small `monitor.json` through `BenchmarkMonitor`; the
dashboard reads that state and the normal result ledger. Its process is started through
`deployment_manager`, so port allocation, health checks, persistence, and stopping use
the same lifecycle as every other deployed service.

The Workflow renderer reads the embedded `<workflow>` element without modifying it, so
the same complete HTML file remains valid input to `WorkflowCompiler` and a standalone
browser document.
