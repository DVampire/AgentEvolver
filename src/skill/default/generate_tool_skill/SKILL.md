---
name: generate_tool_skill
description: Guides an agent through generating a new tool Python class. Use when asked to create a new tool.
version: 1.0.0
type: worker
license: N/A
category: generation
requirements: [cpu]
metadata: {}
---

# Generate Tool Skill

Creates a new tool class under `extension/tool/` following the project's tool convention.

## Instructions

### Step 1: Determine the tool name

Infer a `snake_case` name from the task (e.g. `weather_tool`). The class name is `PascalCase` (e.g. `WeatherTool`).

### Step 2: Read the template

Read the template file at `{skill_dir}/references/tool_template.py` to understand the required structure.

### Step 3: Write the tool file

Write the new tool to `{project_root}/extension/tool/{tool_name}.py`.

Rules:
- Always use **single quotes** for all string literals — the code is stored in JSON and double quotes cause `SyntaxError`.
- Set `require_grad = True` so the tool can be evolved later.
- The `name` field must exactly match the filename without `.py`.
- Implement `async def __call__` returning `Response(type=ResponseType.TOOL, success=..., message=..., data={...})` — results go in `data` (a dict), NOT in `extra`.

### Step 4: Verify syntax

Run: `python -m py_compile {project_root}/extension/tool/{tool_name}.py && echo "OK"`

Fix any syntax errors before proceeding.

### Step 5: Call done_tool

Include the file path in `reasoning` so auto-registration works (the ExtensionManager registers
it and archives the version automatically — there is no `__init__.py` to edit):
`reasoning: "Generated extension/tool/{tool_name}.py. Syntax verified."`

## Workflow

```
- [ ] Step 1: Determine tool name (snake_case) and class name (PascalCase)
- [ ] Step 2: Read references/tool_template.py
- [ ] Step 3: Write extension/tool/{tool_name}.py
- [ ] Step 4: Verify syntax with py_compile
- [ ] Step 5: Call done_tool with file path in reasoning
```

## Output Template

```
Generated tool: {tool_name}
File: extension/tool/{tool_name}.py
Class: {ToolClass}
Description: {one-line description}
```
