---
name: queue
description: "Provides the reusable `AsyncQueue` primitive used by asynchronous framework components."
version: 0.1.0
type: module
category: queue
requirements: []
metadata:
  tracks_package_version: true
---
# Queue

Provides the reusable `AsyncQueue` primitive used by asynchronous framework components.

The implementation is intentionally transport-agnostic and lives in `types.py`. Higher-level
mailbox ownership and agent lifecycle semantics belong to Runtime.
