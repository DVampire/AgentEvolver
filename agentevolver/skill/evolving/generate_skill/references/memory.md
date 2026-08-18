# Writing a memory system

## What it is

A memory system is one Python file, `{extension_root}/memory/{name}.py`.

**The contract**: the read and write interface the agent loop calls each step. Changing its shape
changes every agent configured with `use_memory`.

A single skill for the full lifecycle of **memory systems**: creating, improving, and evaluating them. A memory system decides **what an agent still knows** on its next step: it consumes the session's event stream and renders a bounded view of it back into the prompt. Everything the agent has done that is not rendered is, in effect, forgotten.

## Framework conventions (read once)

A memory system is a **single Python file** (like a tool, unlike an environment):
```
{extension_root}/memory/{name}.py
```
**Registration is automatic via a hook**: after writing the file, include its path in your `done_tool` reasoning — the `memory_registration_hook` registers it.

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
