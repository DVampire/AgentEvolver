# Memory

The full lifecycle of one component type: what a memory is, how to write one, how to
change one, and how to judge one. The contract below holds for all three.

## What it is

A memory system is one Python file, `{extension_root}/memory/{name}.py`.

**The contract**: the read and write interface the agent loop calls each step. Changing its shape
changes every agent configured with `use_memory`.

A memory system decides **what an agent still knows** on its next step: it consumes the session's event stream and renders a bounded view of it back into the prompt. Everything the agent has done that is not rendered is, in effect, forgotten.

## Layout

A memory system is a **single Python file** (like a tool, unlike an environment):
```
{extension_root}/memory/{name}.py
```
**Registration is automatic via a hook**: after writing the file, include its path in your `done_tool` reasoning — the `registration_hook` registers it.

## Writing a new one

### The Python class

Subclass `TieredMemory` and override how the accumulated state is rendered. The base
class already handles event ingestion (`emit`) and retrieval (`get`); what a new
memory system contributes is **selection and presentation** — which of the session's
records survive into the next prompt, and in what shape.

```python
from typing import Any
from pydantic import Field

from agentevolver.memory.default.tiered import TieredMemory, _SessionState
from agentevolver.registry import MEMORY_SYSTEM

@MEMORY_SYSTEM.register_module(force=True)
class MyMemory(TieredMemory):
    """One-line purpose — becomes the description if none is given."""

    name: str = Field(default="my_memory")
    description: str = Field(default="What this memory keeps and why.")
    enable_evolving: bool = Field(default=True)

    def _render(self, state: _SessionState) -> str:
        """Return the text injected into the agent's next prompt."""
        return "\n".join(r.as_line() for r in state.recent)
```

- `name` must match the file stem (`my_memory.py` → `my_memory`).
- `enable_evolving: bool = Field(default=True)` — required, or the component cannot be optimized later.
- Keep `_render` **bounded**: an unbounded transcript defeats the purpose and will blow the context window. Prefer selecting/summarizing over dumping.
- `prompt_readable = False` only if `get()` returns markup rather than prompt-ready text.

### Verify and register

After writing: `python -m py_compile /abs/path/{name}.py`. When it compiles, put the
file path in your `done_tool` reasoning so the hook registers it.

---

## Improving an existing one

The target is named in the task. Call `inspect_tool` (`capability_type="memory"`) FIRST for its file path and `enable_evolving` — if `enable_evolving=False`, the memory system is frozen; do NOT edit it, report and stop. Read the file before editing; make the smallest correct change; preserve `@MEMORY_SYSTEM.register_module`, the class `name`, and the existing method signatures unless the task requires changing them. Verify with `py_compile`, then re-register via the file path in `done_tool` reasoning.

Typical improvements, in order of how often they matter:
- retaining a class of fact that was being dropped (the usual cause of a late-session failure)
- summarizing instead of truncating, so old steps degrade gracefully rather than vanish
- tightening an unbounded section that crowds out everything else

---

## Evaluating one

Call `inspect_tool` with `capability_type="memory"` and the target name for its registry facts. Score across:
1. **Interface Compliance** — `@MEMORY_SYSTEM.register_module`, subclasses `TieredMemory`/`Memory`, `name` matches the file stem, `enable_evolving` declared.
2. **Code Quality** — valid, clean, no unbounded growth, per-session state correctly keyed.
3. **Retention Quality** — does what it keeps actually serve the next step? Is anything load-bearing dropped? Is anything useless retained?
4. **Boundedness** — does the rendered view stay within a sane size as the session grows?
5. **Integration** — the component shows as registered, and `get()` returns usable text.

The decisive question is not "is the code tidy" but **"after N steps, does the agent still know what it needs?"**

---
