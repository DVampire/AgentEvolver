---
name: generate_environment_skill
description: Guides an agent through generating a new environment (a directory holding environment.py + ENVIRONMENT.md, plus a config dict). Use when asked to create a new environment.
version: 1.0.0
type: worker
license: N/A
category: generation
requirements: [cpu]
metadata: {}
---

# Generate Environment Skill

Creates a new environment under `extension/environment/{name}/` following the project's environment convention.

An **environment** is an action provider: a registered class exposing actions (methods decorated with `@environment_manager.action`) plus a `get_state` method and `initialize`/`cleanup` lifecycle hooks. Its rules and per-action docs live in an **ENVIRONMENT.md** (there is NO `get_rules()` method). It is NOT LLM-driven and has no HTML prompt. Generation produces a **directory with 2 files, plus 1 config**:
- `extension/environment/{name}/environment.py` — the Python class (execution only)
- `extension/environment/{name}/ENVIRONMENT.md` — metadata + rules + per-action docs (this is injected into the agent's prompt)
- `configs/environments/{name}.py` — the config dict

## Instructions

### Step 1: Read the templates

Read `{skill_dir}/references/environment_template.py` (the Python class) and `{skill_dir}/references/ENVIRONMENT.md` (the metadata/rules/docs) to learn the required structure.

### Step 2: Write the Python class file

Write to `{project_root}/extension/environment/{environment_name}/environment.py`. The file MUST be named `environment.py`, inside a directory named after the environment.

Rules:
- Use **single quotes** for all string literals.
- Decorate the class with `@ENVIRONMENT.register_module(force=True)`.
- Inherit from `Environment`.
- Define fields: `name` (snake_case, matches the directory name), `description`, `metadata` (just `{'has_vision': ...}`), `require_grad = True`.
- `__init__(self, base_dir=None, **kwargs)` must call `super().__init__(**kwargs)`. Keep it lightweight — start heavy resources (servers, browsers, sockets) in `initialize()`, not `__init__`.
- Implement `async def initialize(self)` and `async def cleanup(self)`.
- Expose at least one action: an `async` method decorated with `@environment_manager.action(name=..., description=...)`, accepting `**kwargs`, returning `{'success': bool, 'message': str, 'extra': dict}`.
- Implement `async def get_state(self, **kwargs)` returning `{'state': <text>, 'extra': {...}}`.
- Do NOT write a `get_rules()` method — the rules live in ENVIRONMENT.md.
- For concurrency-safe environments, key per-session state by `ctx.id` (the `ctx` keyword is injected into every action and `get_state`).

### Step 3: Write ENVIRONMENT.md

Write to `{project_root}/extension/environment/{environment_name}/ENVIRONMENT.md`.
- YAML frontmatter: `name` (matches the class `name` field), `description`, `version` (1.0.0), `type` (worker).
- Markdown body: a `## State` section, a `## Vision` section, a `## Actions` section documenting each action's parameters, and an `## Interaction` section with a JSON call example. This whole body is injected into the agent's prompt, so make it clear and complete.

### Step 4: Write the config dict

Write to `{project_root}/configs/environments/{environment_name}.py`. The dict key must equal the environment `name` (the framework looks up `config[<name>]` during registration). Example:

```python
my_environment = dict(
    base_dir='environment/my_environment',
    require_grad=True,
    # ... any constructor kwargs your __init__ accepts ...
)
```

### Step 5: Verify syntax

Run: `python -m py_compile {project_root}/extension/environment/{environment_name}/environment.py && echo "OK"`

### Step 6: Call done_tool

Specify the environment DIRECTORY path in `reasoning` so it is auto-registered (the
ExtensionManager loads the class from environment.py, reads ENVIRONMENT.md for the
rules/docs, and archives the version automatically):
`reasoning: "extension/environment/{name}"`

## Workflow

```
- [ ] Step 1: Read references/environment_template.py and references/ENVIRONMENT.md
- [ ] Step 2: Write extension/environment/{environment_name}/environment.py
- [ ] Step 3: Write extension/environment/{environment_name}/ENVIRONMENT.md
- [ ] Step 4: Write configs/environments/{environment_name}.py
- [ ] Step 5: Verify syntax with py_compile
- [ ] Step 6: Call done_tool with the environment directory path in reasoning
```

## Output Template

```
Generated environment: {environment_name}
Directory: extension/environment/{environment_name}/
Python: environment.py
Docs: ENVIRONMENT.md
Config: configs/environments/{environment_name}.py
Actions: {comma-separated action names}
```
