# Improving an environment

## What it is

An environment is a directory, `{extension_root}/environment/{name}/`, holding
`environment.py` (the class, exposing `@action` methods) and `ENVIRONMENT.md`. The loader wants
the directory and reads that fixed entry filename itself.

**The contract**: every action returns **text**. An action that returns a bare object leaves the
agent holding `None` and spinning.

## Improving an environment

The target is named in the task. Call `inspect_tool` (`capability_type="environment"`) FIRST for its file paths and `enable_evolving` — if `enable_evolving=False`, the environment is frozen; do NOT edit it, report and stop. Read `environment.py` (and ENVIRONMENT.md) before editing; make the smallest correct change; preserve `@ENVIRONMENT.register_module`, the class `name`, and existing action names/signatures unless the task requires changing them; keep the manifest's Actions section in sync with the code. Verify with `py_compile`, then re-register via the directory path in `done_tool` reasoning.

---
