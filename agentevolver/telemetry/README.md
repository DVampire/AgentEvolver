---
name: telemetry
description: "Exports AgentEvolver Trace events to optional observability backends without changing execution semantics or replacing the durable trace log."
version: 1.0.0
type: module
category: observability
requirements: []
metadata: {}
---
# Telemetry

Exports the framework's authoritative Trace events to optional observability backends.
Exporter failures are isolated from agent execution, so telemetry can be enabled or removed
without changing task outcomes.

| File | Responsibility |
|---|---|
| `otel.py` | Converts paired lifecycle events into OpenTelemetry spans and flushes them |
| `__init__.py` | Exposes the optional exporter bridge |

Trace remains the durable source of truth. Telemetry only projects those records to external
systems for operational inspection.
