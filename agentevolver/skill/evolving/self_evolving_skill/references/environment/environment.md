# Environment

The full lifecycle of one component type: what a environment is, how to write one, how to
change one, and how to judge one. The contract below holds for all three.

## What it is

An environment is a directory, `{extension_root}/environment/{name}/`, holding
`environment.py` (the class, exposing `@action` methods) and `ENVIRONMENT.md`. The loader wants
the directory and reads that fixed entry filename itself.

**The contract**: every action returns **text**. An action that returns a bare object leaves the
agent holding `None` and spinning.

An environment is a stateful Python class over the shared base `Environment` that exposes named **actions** an agent can call, paired with an `ENVIRONMENT.md` manifest.

## Layout

An environment is a directory: `{extension_root}/environment/{name}/`
```
{name}/
├── environment.py    # REQUIRED — the Python class (registered) with @action methods
├── ENVIRONMENT.md    # REQUIRED — YAML frontmatter + body (State / Vision / Actions)
└── __init__.py       # imports the class so it registers on load
```
**Registration is automatic via a hook**: after writing the files, include the environment directory (or `environment.py`) path in your `done_tool` reasoning — the `registration_hook` registers it.

**Start from the templates**: read `references/environment/template.py` (the class) and `references/environment/template-manifest.md` (the manifest), copy them, and adapt.

## Writing a new one

### The Python class

```python
from typing import Any, Dict
from pydantic import ConfigDict, Field
from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.registry import ENVIRONMENT

@ENVIRONMENT.register_module(force=True)
class MyEnvironment(Environment):
    """One-line purpose."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def initialize(self) -> None:
        """Set up resources (called once before first use)."""

    async def cleanup(self) -> None:
        """Tear down resources."""

    @environment_manager.action(
        name="do_thing",
        description="What this action does and when to use it.",
    )
    async def do_thing(self, ctx, some_arg: str, **kwargs):
        """Perform the action; return a result (string/dict/base64 image for vision envs)."""
        return {"ok": True, "echo": some_arg}
```

- Each callable is an **action** declared with `@environment_manager.action(name=..., description=...)`.
- State lives on the instance (that's what makes an environment stateful, unlike a stateless tool). Key per-session state by `ctx` when the environment serves concurrent sessions.
- If the environment returns images (screenshots), it's a **vision** environment — say so in ENVIRONMENT.md so the agent knows to inspect the image.

### The ENVIRONMENT.md manifest

```markdown
---
name: my_environment
description: One line — what the environment is and when to use it.
version: 1.0.0
type: worker
---

<environment_my_environment>

### State

What the environment holds/simulates and how it behaves.

### Vision

(Only if actions return images.) What the visual output is and how to use it.

### Actions

#### do_thing
What it does, its arguments, and when to call it.
```

The body documents the environment's state, (optional) vision, and each action — this is what an agent reads before acting. Keep action docs concrete.

#### Verify and register

After writing: `python -m py_compile /abs/path/environment.py`. When it compiles, put the environment directory path in your `done_tool` reasoning so the hook registers it.

---

## Improving an existing one

The target is named in the task. Call `inspect_tool` (`capability_type="environment"`) FIRST for its file paths and `enable_evolving` — if `enable_evolving=False`, the environment is frozen; do NOT edit it, report and stop. Read `environment.py` (and ENVIRONMENT.md) before editing; make the smallest correct change; preserve `@ENVIRONMENT.register_module`, the class `name`, and existing action names/signatures unless the task requires changing them; keep the manifest's Actions section in sync with the code. Verify with `py_compile`, then re-register via the directory path in `done_tool` reasoning.

---

## Evaluating one

Call `inspect_tool` (`capability_type="environment"`) on the target for its registry facts (registered / enable_evolving / version / file paths). Score across:
1. **Interface Compliance** — `@ENVIRONMENT.register_module`, subclass `Environment`, actions declared with `@environment_manager.action`, `initialize`/`cleanup` present where resources are used.
2. **Code Quality** — valid, clean, proper resource handling and error handling; per-session state correctly keyed by `ctx`.
3. **Manifest Quality** — ENVIRONMENT.md has the required frontmatter and a body documenting State / (Vision) / every Action.
4. **Integration** — `inspect_tool` (`capability_type="environment"`) shows it registered.
5. **Execution** — actions have a valid path; where feasible, exercise an action and check the result.

---
