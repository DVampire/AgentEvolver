---
name: hook
description: "Provides lifecycle interception points for tracing, compaction, registration, promotion, and other cross-cutting behavior."
version: 0.1.0
type: module
category: hook
requirements: []
metadata:
  tracks_package_version: true
---
# Hook

Provides lifecycle interception points for tracing, compaction, registration, promotion,
and other cross-cutting behavior.

| File | Responsibility |
|---|---|
| `types.py` | Events, decisions, contexts, and hook contracts |
| `context.py` | Hook configuration and registration state |
| `server.py` | Ordered hook dispatch facade |
| `promotion.py` | Registration/promotion helpers |
| `default/` | Built-in hooks |

Hooks observe or gate lifecycle events; core business logic stays in the owning module.
