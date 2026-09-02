---
name: hook_default
description: "Registers tracing, trajectory, compaction, project learning, constraints, and capability registration hooks."
version: 1.0.0
type: collection
category: hook
requirements: []
metadata: {}
---
# Built-in hooks

Registers the built-in lifecycle adapters. Session memory consumes the exact numbered
TraceEvent emitted by `TraceHook`; it is not a second hook because rebuilding a look-alike
event would break trace identity. `ProjectMemoryHook` is distinct: after successful task
completion it projects verified evidence into durable cross-session project memory.

Files use responsibility names (`trace.py`, `trajectory.py`, `project_memory.py`); the
classes carry the `Hook` suffix and registered runtime names use `_hook`, except `compact`,
whose short name is the stable checkpoint strategy identifier used by TieredMemory.
