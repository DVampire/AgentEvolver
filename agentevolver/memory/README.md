---
name: memory
description: "Stores and retrieves durable context derived from agent execution history. The default implementation provides a general memory system while preserving a common Manager API."
version: 0.1.0
type: module
category: memory
requirements: []
metadata:
  tracks_package_version: true
---
# Memory

Stores and retrieves durable context derived from agent execution history. The default
implementation provides a general memory system while preserving a common Manager API.

| File | Responsibility |
|---|---|
| `types.py` | Memory and configuration contracts |
| `context.py` | Memory registry and instance lifecycle |
| `server.py` | Public `memory_manager` facade |
| `default/` | Built-in memory systems |

Memory supplies relevant context; Prompt decides presentation and Agent decides when it is
used.
