---
name: generate_environment_skill
description: Guides an agent through generating a new environment (Python class + config dict). Use when asked to create a new environment.
version: 1.0.0
type: sop
---

# Generate Environment Skill

Creates a new environment under `src/environment/extended/` following the project's environment convention.

An **environment** is an action provider: a registered class exposing actions (methods decorated with `@environment_manager.action`) plus a `get_state` method and `initialize`/`cleanup` lifecycle hooks. It is NOT LLM-driven and has **no HTML prompt**. Generation produces **2 files**:
- `src/environment/extended/{name}.py` — the Python class
- `configs/environments/{name}.py` — the config dict

## Instructions

### Step 1: Read the template

Read `{skill_dir}/references/environment_template.py` to learn the required structure.

### Step 2: Write the Python class file

Write to `{project_root}/src/environment/extended/{environment_name}.py`.

Rules:
- Use **single quotes** for all string literals.
- Decorate the class with `@ENVIRONMENT.register_module(force=True)`.
- Inherit from `Environment`.
- Define fields: `name` (snake_case, matches the file name), `description`, `metadata`, `require_grad = True`.
- `__init__(self, base_dir=None, **kwargs)` must call `super().__init__(**kwargs)`. Keep it lightweight — start heavy resources (servers, browsers, sockets) in `initialize()`, not `__init__`.
- Implement `async def initialize(self)` and `async def cleanup(self)`.
- Expose at least one action: an `async` method decorated with `@environment_manager.action(name=..., description=...)`, accepting `**kwargs`, returning `{'success': bool, 'message': str, 'extra': dict}`. Write descriptions clear enough for an LLM to choose and call the action.
- Implement `async def get_state(self, **kwargs)` returning `{'state': <text>, 'extra': {...}}`.
- For concurrency-safe environments, key per-session state by `ctx.id` (the `ctx` keyword is injected into every action and `get_state`).

### Step 3: Write the config dict

Write to `{project_root}/configs/environments/{environment_name}.py`. The dict key must equal the environment `name` (the framework looks up `config[<name>]` during registration). Example:

```python
my_environment = dict(
    base_dir='environment/my_environment',
    require_grad=True,
    # ... any constructor kwargs your __init__ accepts ...
)
```

### Step 4: Update `__init__.py`

Edit `{project_root}/src/environment/extended/__init__.py` to import the new class so it is registered on package import.

### Step 5: Verify syntax

Run: `python -m py_compile {project_root}/src/environment/extended/{environment_name}.py && echo "OK"`

### Step 6: Call done_tool

Specify the Python file path in `reasoning` so the environment is auto-registered:
`reasoning: "src/environment/extended/{name}.py"`

## Workflow

```
- [ ] Step 1: Read references/environment_template.py
- [ ] Step 2: Write src/environment/extended/{environment_name}.py
- [ ] Step 3: Write configs/environments/{environment_name}.py
- [ ] Step 4: Update src/environment/extended/__init__.py
- [ ] Step 5: Verify syntax with py_compile
- [ ] Step 6: Call done_tool with the Python file path in reasoning
```

## Output Template

```
Generated environment: {environment_name}
Python: src/environment/extended/{environment_name}.py
Config: configs/environments/{environment_name}.py
Actions: {comma-separated action names}
```
