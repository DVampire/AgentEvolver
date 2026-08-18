# Improving a tool

## What it is

A tool is one Python file, `{extension_root}/tool/{name}.py`, holding a class over the
shared base `Tool`. An agent calls it with a JSON args object and it returns a `Response`.

**The contract**: the `__call__` signature, and a `Response` with `success`, `message` and
`data` — results go in `data`; `extra` is caller-defined and the framework does not read it.
A failure is *returned* (`success=False`), never raised. Its documentation is its fields:
`_DESCRIPTION` (the call schema), `_GUIDANCE` (carried in the prompt every step) and
`_EXAMPLES` (fetched only by `inspect_tool`).

## Improving a tool

The target is named in the task. Call `inspect_tool` (capability_type="tool") FIRST for its source path and `enable_evolving` — if `enable_evolving=False`, the tool is frozen; do NOT edit it, report and stop. Read the source before editing; make the smallest correct change; preserve `@TOOL.register_module` and `name`; keep `_DESCRIPTION` one line and `_GUIDANCE` / `_EXAMPLES` in place. Verify with `py_compile`, then re-register via the path in `done_tool` reasoning.

---
