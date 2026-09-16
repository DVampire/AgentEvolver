# Tool

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

A tool is one Python file, `{extension_root}/tool/{name}.py`, holding a class over the
shared base `Tool`. An agent calls it with a JSON args object and it returns a `Response`.

**The contract**: the `__call__` signature, and a `Response` with `success`, `message` and
`data` — results go in `data`; `extra` is caller-defined and the framework does not read it.
A failure is *returned* (`success=False`), never raised. Its documentation is its fields:
`_DESCRIPTION` (the call schema), `_GUIDANCE` (carried in the prompt every step) and
`_EXAMPLES` (fetched only by `inspect_tool`).

A tool is a Python class over the shared base `Tool` that an agent invokes with a JSON args object and that returns a `Response`.

## Layout

- A tool is a single Python file: `{extension_root}/tool/{name}.py`.
- **Registration is a call you make**: after writing the file, call `adoption_tool` with `action="register"`, `module="tool"`, the tool name, and its absolute path as `artifact_path`.

**Start from the template**: read `references/tool/template.py`, copy it, and adapt — it already encodes the convention below.

## Writing a new one

### Anatomy: four fields, and one thing you do not write

A tool's documentation is its fields. Three you write, one is derived, and the split
decides what a prompt carries every step versus what an agent fetches when it stops to ask:

| field | who writes it | where it goes |
|---|---|---|
| `_DESCRIPTION` | you | the card's subtitle **and** the call schema's `description` |
| `_GUIDANCE` | you | the prompt, for every resident tool, every step |
| `_EXAMPLES` | you | only `inspect_tool` — worth reading before a first call, not worth carrying afterwards |
| **parameters** | **nobody** | derived from `__call__`'s signature and its `Args:` docstring, and sent in the request's own `tools` array |

**Do not write a `## Parameters` block.** The arguments a model is checked against come
from the signature; a prose copy is a third spelling of one contract, and it is the copy
that goes stale. Document each argument in the `Args:` docstring instead — that is what
becomes the schema's per-argument description.

**Do not write a `## Function` block** either. That was the description again.

The executable [template.py](template.py) is the only code scaffold; keep its example,
argument schema and runtime declarations together when adapting it.

Design principles:
- `__call__` must return a `Response` (`success`, `message`, optional `data`); catch expected failures and return `success=False` with an actionable message rather than raising.
- Keep args explicit and JSON-friendly. Document every arg in `__call__`'s Google-style
  `Args:` docstring — that is what becomes the schema's per-argument description.
- If the tool needs the current session, accept `ctx` via `**kwargs`. Do heavyweight imports **inside** `__call__` to avoid circular imports at module load.

### Concurrency and effects

The template's pure operation sets class `concurrent=True` and `mutates=False`.
`concurrent` allows simultaneous calls on one instance; `mutates`/`will_mutate(arguments)`
tells the Agent whether calls are reads. Reassess both when adding I/O or mutation. Tools
do not use the Environment action decorator or an invented `parallel_safe` class field.
Keep ctx, arguments and intermediate results local. For shared clients/output files,
retain base exclusion or override `resource_claims(ctx, arguments)` with verified scopes;
workspace writes still need coordination even when instances are reentrant. Call through
the Tool Manager and verify both independence and same-resource ordering.

### Verify and register

After writing: `python -m py_compile /abs/path/{name}.py`. When it compiles, register it with `adoption_tool` (`action="register"`, `artifact_path` = that path).

---

## Improving an existing one

Preserve `@TOOL.register_module`, the registered `name`, compatible arguments and `Response`
semantics. Keep `_DESCRIPTION` one line and `_GUIDANCE` / `_EXAMPLES` consistent with the
actual schema. Reproduce the failing call before changing its implementation or docs.
Use the common staging and repair loop rather than editing the installed Python file.

## Evaluating one

Call `inspect_tool` (capability_type="tool") on the target — it returns the full instruction plus registry facts (version, enable_evolving, source path). Check the type-specific requirements:
1. **Interface Compliance** — `@TOOL.register_module`, subclass `Tool`, has `name`/`description`/`instruction`, `__call__` returns a `Response`.
2. **Code Quality** — valid, clean, proper error handling (failures returned as `success=False`, not raised).
3. **Documentation Quality** — `_GUIDANCE` says what the schema cannot; every argument has
   an `Args:` line; each entry of `_EXAMPLES` is valid JSON. No `## Parameters` or
   `## Function` block: both restate something the model is already sent.
4. **Integration** — `inspect_tool` (capability_type="tool") shows it registered.
5. **Execution** — call the registered tool with representative valid and invalid inputs; verify result values, actual effects and actionable failure responses. Include a distinct reuse/regression input in the common comparison.

---
