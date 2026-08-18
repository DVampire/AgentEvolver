# Evaluating a tool

## What it is

A tool is one Python file, `{extension_root}/tool/{name}.py`, holding a class over the
shared base `Tool`. An agent calls it with a JSON args object and it returns a `Response`.

**The contract**: the `__call__` signature, and a `Response` with `success`, `message` and
`data` — results go in `data`; `extra` is caller-defined and the framework does not read it.
A failure is *returned* (`success=False`), never raised. Its documentation is its fields:
`_DESCRIPTION` (the call schema), `_GUIDANCE` (carried in the prompt every step) and
`_EXAMPLES` (fetched only by `inspect_tool`).

## Evaluating a tool

Call `inspect_tool` (capability_type="tool") on the target — it returns the full instruction plus registry facts (version, enable_evolving, source path). Score across:
1. **Interface Compliance** — `@TOOL.register_module`, subclass `Tool`, has `name`/`description`/`instruction`, `__call__` returns a `Response`.
2. **Code Quality** — valid, clean, proper error handling (failures returned as `success=False`, not raised).
3. **Documentation Quality** — `_GUIDANCE` says what the schema cannot; every argument has
   an `Args:` line; each entry of `_EXAMPLES` is valid JSON. No `## Parameters` or
   `## Function` block: both restate something the model is already sent.
4. **Integration** — `inspect_tool` (capability_type="tool") shows it registered.
5. **Execution** — a valid call path; where feasible, run the tool on a sample input and check the `Response`.

---
