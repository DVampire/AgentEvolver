# Evaluating a memory system

## What it is

A memory system is one Python file, `{extension_root}/memory/{name}.py`.

**The contract**: the read and write interface the agent loop calls each step. Changing its shape
changes every agent configured with `use_memory`.

## Evaluating a memory system

Call `inspect_memory_tool` (or `inspect_tool` with capability_type="tool" on the name) for its registry facts. Score across:
1. **Interface Compliance** — `@MEMORY_SYSTEM.register_module`, subclasses `TieredMemory`/`Memory`, `name` matches the file stem, `enable_evolving` declared.
2. **Code Quality** — valid, clean, no unbounded growth, per-session state correctly keyed.
3. **Retention Quality** — does what it keeps actually serve the next step? Is anything load-bearing dropped? Is anything useless retained?
4. **Boundedness** — does the rendered view stay within a sane size as the session grows?
5. **Integration** — the component shows as registered, and `get()` returns usable text.

The decisive question is not "is the code tidy" but **"after N steps, does the agent still know what it needs?"**

---
