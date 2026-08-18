# Evaluating an environment

## What it is

An environment is a directory, `{extension_root}/environment/{name}/`, holding
`environment.py` (the class, exposing `@action` methods) and `ENVIRONMENT.md`. The loader wants
the directory and reads that fixed entry filename itself.

**The contract**: every action returns **text**. An action that returns a bare object leaves the
agent holding `None` and spinning.

## Evaluating an environment

Call `inspect_environment_tool` on the target for its registry facts (registered / enable_evolving / version / file paths). Score across:
1. **Interface Compliance** — `@ENVIRONMENT.register_module`, subclass `Environment`, actions declared with `@environment_manager.action`, `initialize`/`cleanup` present where resources are used.
2. **Code Quality** — valid, clean, proper resource handling and error handling; per-session state correctly keyed by `ctx`.
3. **Manifest Quality** — ENVIRONMENT.md has the required frontmatter and a body documenting State / (Vision) / every Action.
4. **Integration** — `inspect_environment_tool` shows it registered.
5. **Execution** — actions have a valid path; where feasible, exercise an action and check the result.

---
