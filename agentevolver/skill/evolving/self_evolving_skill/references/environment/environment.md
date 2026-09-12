# Environment

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

An environment is a directory, `{extension_root}/environment/{name}/`, holding
`environment.py` (the class, exposing `@action` methods) and `ENVIRONMENT.md`. The loader wants
the directory and reads that fixed entry filename itself.

**The contract**: return a `Response`, a mapping with `success`, `message` and optional `data`,
or plain text for a successful action. The manager normalizes mappings into visible text.
Use `success=False` for failures; a JSON string containing `ok: false` is still a successful
text response to the runtime. Keep large results in artifacts and return their paths.

An environment is a stateful Python class over the shared base `Environment` that exposes named **actions** an agent can call, paired with an `ENVIRONMENT.md` manifest.

## Layout

An environment is a directory: `{extension_root}/environment/{name}/`
```
{name}/
├── environment.py    # REQUIRED — the Python class (registered) with @action methods
├── ENVIRONMENT.md    # REQUIRED — YAML frontmatter + body (State / Vision / Actions)
└── __init__.py       # imports the class so it registers on load
```
**Registration is a call you make**: after writing the files, call `adoption_tool` with `action="register"`, `module="environment"`, the environment name, and its directory as `artifact_path`.

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

    name: str = Field(default="my_environment")
    description: str = Field(default="Echo input for a bounded interface example.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def initialize(self) -> None:
        """Set up resources (called once before first use)."""

    async def cleanup(self) -> None:
        """Tear down resources."""

    async def get_state(self, ctx=None, **kwargs):
        return {"success": True, "state": "Ready"}

    @environment_manager.action(
        name="do_thing",
        description="What this action does and when to use it.",
        read_only=True, destructive=False, idempotent=True, open_world=False,
    )
    async def do_thing(self, ctx, some_arg: str, **kwargs):
        """Echo without changing state; adjust declarations when adapting this example."""
        return {"success": True, "message": some_arg}
```

- Each callable is an **action** declared with `@environment_manager.action(name=..., description=...)`.
- Declare actual `read_only`, `destructive`, `idempotent` and `open_world` effects on each
  action. Missing declarations can block execution under the permission policy; registration alone does not make an action
  executable. Local state mutation is not read-only. For file operations, also declare
  `permission_op` and `permission_target` naming the target path argument. Keep external writes
  and destructive behavior accurately declared rather than changing flags to bypass a refusal.
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

After writing, compile and instantiate the class with its intended configuration. Check
`get_state(ctx=...)`, action effects, successful output and a real failure response. Compilation
alone does not catch required Pydantic fields or missing effect declarations. Then register
with `adoption_tool`, `action="register"`, `module="environment"` and the directory as
`artifact_path`; inspect and exercise the returned native actions before adoption.

---

## Improving an existing one

Read `environment.py`, `ENVIRONMENT.md` and relevant initialization/state code. Preserve
`@ENVIRONMENT.register_module`, the registered name and compatible action signatures. Keep
Actions, State and Vision documentation aligned with actual behavior and effects. Reproduce
the failing state transition in isolated state, then use the common repair loop. Registration
does not update an existing environment instance; inspect binding/version and use a supported
fresh instance or handoff before attributing results to the candidate.

## Evaluating one

Call `inspect_tool` (`capability_type="environment"`) on the target for its registry facts (registered / enable_evolving / version / file paths). Check the type-specific requirements:
1. **Interface Compliance** — `@ENVIRONMENT.register_module`, subclass `Environment`, actions declared with `@environment_manager.action`, `initialize`/`cleanup` present where resources are used.
2. **Code Quality** — valid, clean, proper resource handling and error handling; per-session state correctly keyed by `ctx`.
3. **Manifest Quality** — ENVIRONMENT.md has the required frontmatter and a body documenting State / (Vision) / every Action.
4. **Integration** — `inspect_tool` (`capability_type="environment"`) shows it registered.
5. **Execution** — initialize the candidate, run representative native actions and inspect
   resulting state/observations. Test a valid operation, a failed operation and a relevant
   state transition/reuse case. Verify cleanup, reset and isolation where applicable;
   a permanently blocked stub does not implement the declared operation.

---
