# Improving a memory system

## What it is

A memory system is one Python file, `{extension_root}/memory/{name}.py`.

**The contract**: the read and write interface the agent loop calls each step. Changing its shape
changes every agent configured with `use_memory`.

## Improving a memory system

The target is named in the task. Call `inspect_memory_tool` FIRST for its file path and `enable_evolving` — if `enable_evolving=False`, the memory system is frozen; do NOT edit it, report and stop. Read the file before editing; make the smallest correct change; preserve `@MEMORY_SYSTEM.register_module`, the class `name`, and the existing method signatures unless the task requires changing them. Verify with `py_compile`, then re-register via the file path in `done_tool` reasoning.

Typical improvements, in order of how often they matter:
- retaining a class of fact that was being dropped (the usual cause of a late-session failure)
- summarizing instead of truncating, so old steps degrade gracefully rather than vanish
- tightening an unbounded section that crowds out everything else

---
