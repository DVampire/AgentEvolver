---
name: generate_agent_skill
description: Guides an agent through generating a new agent (Python class + optional HTML prompt). Use when asked to create a new agent.
version: 1.0.0
type: sop
---

# Generate Agent Skill

Creates a new agent under `src/agent/extended/` following the project's agent convention.

## Instructions

### Step 1: Decide agent type

**Tool-calling agent** — LLM drives each step freely. Generates 3 files:
- `src/agent/extended/{name}.py`
- `src/prompt/default/{name}.html`
- `configs/agents/{name}.py`

**Workflow agent** — fixed sequential steps in Python. Generates 2 files:
- `src/agent/extended/{name}.py`
- `configs/agents/{name}.py`

Choose tool-calling for open-ended tasks; workflow for fixed, predictable pipelines.

### Step 2: Read the template

- Tool-calling: read `{skill_dir}/resources/tool_calling_template.py`
- Workflow: read `{skill_dir}/resources/workflow_template.py`
- HTML prompt (tool-calling only): read `{skill_dir}/resources/html_prompt_template.html`

### Step 3: Write the Python class file

Write to `{project_root}/src/agent/extended/{agent_name}.py`.

Rules:
- Use **single quotes** for all string literals.
- Decorate with `@AGENT.register_module(force=True)`.
- `_think_and_act` is inherited from the base `Agent` class — **do NOT redefine it**.
- Only implement: `__init__`, `_get_agent_context`, `__call__`.
- Set `require_grad = True`.

### Step 4: Write the HTML prompt (tool-calling only)

Write to `{project_root}/src/prompt/default/{agent_name}.html` based on `html_prompt_template.html`.

Fill in: `<profile>`, `<domain-rules>`, `<output-schema>` with agent-specific content.
Keep `{{ agent_context }}`, `{{ tool_context }}`, `{{ skill_context }}` as-is.

### Step 5: Write the config dict

Write to `{project_root}/configs/agents/{agent_name}.py`.

### Step 6: Update `__init__.py`

Edit `{project_root}/src/agent/extended/__init__.py` to import the new class.

### Step 7: Verify syntax

Run: `python -m py_compile {project_root}/src/agent/extended/{agent_name}.py && echo "OK"`

### Step 8: Call done_tool

Specify type and file paths in `reasoning`:
`reasoning: "type=tool_calling src/agent/extended/{name}.py src/prompt/default/{name}.html"`

## Workflow

```
- [ ] Step 1: Decide type (tool_calling / workflow)
- [ ] Step 2: Read appropriate template(s) from resources/
- [ ] Step 3: Write src/agent/extended/{agent_name}.py
- [ ] Step 4: Write src/prompt/default/{agent_name}.html (tool-calling only)
- [ ] Step 5: Write configs/agents/{agent_name}.py
- [ ] Step 6: Update src/agent/extended/__init__.py
- [ ] Step 7: Verify syntax with py_compile
- [ ] Step 8: Call done_tool with type and file paths in reasoning
```

## Output Template

```
Generated agent: {agent_name}
Type: {tool_calling | workflow}
Python: src/agent/extended/{agent_name}.py
Prompt: src/prompt/default/{agent_name}.html (if tool-calling)
Config: configs/agents/{agent_name}.py
```
