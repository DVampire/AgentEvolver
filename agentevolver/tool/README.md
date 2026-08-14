---
name: tool
description: "Defines atomic callable capabilities backed by Python implementations. Tool signatures are introspected into native function-calling schemas and routed through `tool_manager`."
version: 1.0.0
type: module
category: tool
requirements: []
metadata: {}
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
orchestration belongs to Workflow. The former `tool/workflow/` location has been retired
(its `todo` tool now lives under `default/`); it was never a public Workflow registry, so
define Workflows in the Workflow module rather than here.

`Tool.call_timeout_seconds` declares what one call of the tool is allowed to cost. The
dispatch funnel reads it from the registry, so the budget sits next to the code that knows
the work; a tool that declares nothing takes the manager default. A tool that also bounds
something internally (`bash_tool.timeout` bounds the child process) should keep the inner
bound smaller, so it returns its own diagnostic rather than being cut off mid-report.
