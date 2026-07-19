---
name: tool
description: "Defines atomic callable capabilities backed by Python implementations. Tool signatures are introspected into native function-calling schemas and routed through `tool_manager`."
version: 0.1.0
type: module
category: tool
requirements: []
metadata:
  tracks_package_version: true
---
# Tool

Defines atomic callable capabilities backed by Python implementations. Tool signatures are
introspected into native function-calling schemas and routed through `tool_manager`.

| Path | Responsibility |
|---|---|
| `types.py` | Tool and configuration contracts |
| `context.py` | Registration, dynamic loading, versions, and instances |
| `server.py` | Public execution API and canonical schemas |
| `default/` | Built-in framework tools, including inspect tools |
| `other/` | Optional integrations |

Tools should remain small and atomic. Reusable guidance belongs to Skill; multi-step
orchestration belongs to Workflow. The legacy `tool/workflow/` location is not a public
Workflow registry and should not receive new Workflow definitions.
