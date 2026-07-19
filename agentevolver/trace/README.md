---
name: trace
description: "Captures structured lifecycle events, persists them, and serves the trace inspection UI."
version: 0.1.0
type: module
category: trace
requirements: []
metadata:
  tracks_package_version: true
---
# Trace

Captures structured lifecycle events, persists them, and serves the trace inspection UI.

| Path | Responsibility |
|---|---|
| `types.py` | Trace event contracts and event factories |
| `writer.py` | Durable event writing |
| `server.py` | Trace manager and UI server lifecycle |
| `app.py` | Trace web application |
| `ui/` | Browser client source |

Trace is observational. It must not change Agent, Runtime, or Workflow execution semantics.
