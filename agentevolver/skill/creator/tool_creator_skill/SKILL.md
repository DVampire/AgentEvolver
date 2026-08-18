---
name: tool_creator_skill
description: Create new tools, improve/optimize existing tools, and evaluate tool quality — the full tool lifecycle in this framework. Use whenever the task involves authoring a new tool (a Python class the agents can call), editing/improving an existing tool, or evaluating/scoring a tool. MetaAgent uses it to orchestrate the create→evaluate→improve loop across sub-agents.
version: 1.0.0
type: [orchestrator, worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Tool Creator

A single skill for the full lifecycle of **tools**: creating, improving, and evaluating them. A tool is a Python class over the shared base `Tool` that an agent invokes with a JSON args object and that returns a `Response`.

## How this skill is used — four roles, one body of knowledge

- **MetaAgent (orchestrator role)** — drives the create→evaluate→improve loop. See **Orchestration**.
- **tool_generate_agent** — reads **Creating a tool**.
- **tool_optimize_agent** — reads **Improving a tool**.
- **tool_evaluate_agent** — reads **Evaluating a tool**.

The sub-agents are headless: each runs one phase autonomously and returns a result.

## Framework conventions (read once)

- A tool is a single Python file: `{extension_root}/tool/{name}.py`.
- **Registration is automatic via a hook**: after writing the file, include its path in your `done_tool` reasoning — the `tool_registration_hook` registers it.

**Start from the template**: read `references/tool_template.py`, copy it, and adapt — it already encodes the convention below.

### Anatomy: four fields, and one thing you do not write

A tool's documentation is its fields. Three you write, one is derived, and the split
decides what a prompt carries every step versus what an agent fetches when it stops to ask:

| field | who writes it | where it goes |
|---|---|---|
| `_DESCRIPTION` | you | the card's subtitle **and** the call schema's `description` |
| `_GUIDANCE` | you | the prompt, for every resident tool, every step |
| `_EXAMPLES` | you | only `inspect_capability_tool` — worth reading before a first call, not worth carrying afterwards |
| **parameters** | **nobody** | derived from `__call__`'s signature and its `Args:` docstring, and sent in the request's own `tools` array |

**Do not write a `## Parameters` block.** The arguments a model is checked against come
from the signature; a prose copy is a third spelling of one contract, and it is the copy
that goes stale. Document each argument in the `Args:` docstring instead — that is what
becomes the schema's per-argument description.

**Do not write a `## Function` block** either. That was the description again.

```python
from typing import Any, Dict, List
from pydantic import Field
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.registry import TOOL

_DESCRIPTION = "One line: what the tool does."

_GUIDANCE = """
When and how to use it; caveats; when NOT to use it. What the call schema cannot say.
"""

_EXAMPLES = [
    '{"name": "my_tool", "args": {"arg_name": "value"}}',
]


@TOOL.register_module(force=True)
class MyTool(Tool):
    """One-line purpose."""
    name: str = "my_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, arg_name: str, **kwargs) -> Response:
        """Do the work. Args mirror the Parameters block."""
        # ... perform the operation ...
        return Response(type=ResponseType.TOOL, success=True, message="result",
                        data={"arg_name": arg_name})
```

Design principles:
- `__call__` must return a `Response` (`success`, `message`, optional `data`); catch expected failures and return `success=False` with an actionable message rather than raising.
- Keep args explicit and JSON-friendly. Document every arg in `__call__`'s Google-style
  `Args:` docstring — that is what becomes the schema's per-argument description.
- If the tool needs the current session, accept `ctx` via `**kwargs`. Do heavyweight imports **inside** `__call__` to avoid circular imports at module load.

### Verify and register

After writing: `python -m py_compile /abs/path/{name}.py`. When it compiles, put the path in your `done_tool` reasoning so the hook registers it.

---

## Evaluating a tool

Call `inspect_capability_tool` (capability_type="tool") on the target — it returns the full instruction plus registry facts (version, enable_evolving, source path). Score across:
1. **Interface Compliance** — `@TOOL.register_module`, subclass `Tool`, has `name`/`description`/`instruction`, `__call__` returns a `Response`.
2. **Code Quality** — valid, clean, proper error handling (failures returned as `success=False`, not raised).
3. **Documentation Quality** — `_GUIDANCE` says what the schema cannot; every argument has
   an `Args:` line; each entry of `_EXAMPLES` is valid JSON. No `## Parameters` or
   `## Function` block: both restate something the model is already sent.
4. **Integration** — `inspect_capability_tool` (capability_type="tool") shows it registered.
5. **Execution** — a valid call path; where feasible, run the tool on a sample input and check the `Response`.

---

## Improving a tool

The target is named in the task. Call `inspect_capability_tool` (capability_type="tool") FIRST for its source path and `enable_evolving` — if `enable_evolving=False`, the tool is frozen; do NOT edit it, report and stop. Read the source before editing; make the smallest correct change; preserve `@TOOL.register_module` and `name`; keep `_DESCRIPTION` one line and `_GUIDANCE` / `_EXAMPLES` in place. Verify with `py_compile`, then re-register via the path in `done_tool` reasoning.

---

## Orchestration (for MetaAgent)

1. **Generate** — dispatch `tool_generate_agent`; it writes the tool file and registers.
2. **Evaluate** — dispatch `tool_evaluate_agent` (optionally after a sample call) to score.
3. **Improve** — dispatch `tool_optimize_agent` with the evaluation; it edits and re-registers.
4. **Repeat** until the tool is good.

## Reference files

- `references/tool_template.py` — a ready-to-copy tool class (the
  `_DESCRIPTION` / `_GUIDANCE` / `_EXAMPLES` split + `__call__` returning a `Response`).
