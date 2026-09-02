---
name: session
description: "Defines shared invocation context and session-scoped project state."
version: 1.0.0
type: module
category: session
requirements: []
metadata: {}
---
# Session

Defines shared invocation context, session-scoped project state, and historical reads.

| File | Responsibility |
|---|---|
| `types.py` | Context contracts and bounded historical-read result models |
| `context.py` | Session sandbox, manifest, input staging, and path binding |
| `server.py` | `SessionManagerServer`: discovery, outline, search, and exact historical reads |

Capability-specific contexts extend these base contracts while retaining consistent session,
workspace, and extra-data semantics.

`SessionManagerServer` creates live Session state, records its identity on first use, and
reads authoritative Trace files directly. Historical reads have no parallel index or
cache, so they belong to the same manager rather than a nested `query` package. Every
result is bounded and reports truncation; partly broken logs remain readable and report
skipped lines.
