---
name: generate_agent_skill
description: Guides an agent through generating a new agent (Python class + optional HTML prompt). Use when asked to create a new agent.
version: 1.0.0
type: worker
---

# Generate Agent Skill

Creates a new agent under `extension/agent/` following the project's agent convention.

## Instructions

### Step 1: Decide agent type

**Tool-calling agent** — LLM drives each step freely. Generates 3 files:
- `extension/agent/{name}.py`
- `extension/prompt/{name}.html`
- `configs/agents/{name}.py`

**Workflow agent** — fixed sequential steps in Python. Generates 2 files:
- `extension/agent/{name}.py`
- `configs/agents/{name}.py`

Choose tool-calling for open-ended tasks; workflow for fixed, predictable pipelines.

### Step 2: Read the template

- Tool-calling: read `{skill_dir}/references/tool_calling_template.py`
- Workflow: read `{skill_dir}/references/workflow_template.py`
- HTML prompt (tool-calling only): read `{skill_dir}/references/html_prompt_template.html`

### Step 3: Write the Python class file

Write to `{project_root}/extension/agent/{agent_name}.py`.

Rules:
- Use **single quotes** for all string literals.
- Decorate with `@AGENT.register_module(force=True)`.
- `_think_and_act` is inherited from the base `Agent` class — **do NOT redefine it**.
- Only implement: `__init__`, `_get_agent_context`, `__call__`.
- Set `require_grad = True`.

### Step 4: Write the HTML prompt (tool-calling only)

Write to `{project_root}/extension/prompt/{agent_name}.html` based on `html_prompt_template.html`.

Fill in: `<profile>`, `<domain-rules>`, `<output-schema>` with agent-specific content.
Keep the `<div class="user">` block as-is: `<agent-context>` with its sub-modules
(`{{ task }}`, `{{ constraint_text }}`, `{{ step_info }}`, `{{ memory_context }}`, `{{ workspace }}`, `{{ errors }}`),
plus `<domain-target>`, `<tool-context>` (`{{ available_tools }}`) and `<skill-context>` (`{{ available_skills }}`) as siblings.

### Step 5: Write the config dict

Write to `{project_root}/configs/agents/{agent_name}.py`.

### Step 6: Verify syntax

Run: `python -m py_compile {project_root}/extension/agent/{agent_name}.py && echo "OK"`

### Step 7: Call done_tool

Specify type and file paths in `reasoning` (the ExtensionManager registers the agent and
its prompt and archives the version automatically — there is no `__init__.py` to edit):
`reasoning: "type=tool_calling extension/agent/{name}.py extension/prompt/{name}.html"`

## Workflow

```
- [ ] Step 1: Decide type (tool_calling / workflow)
- [ ] Step 2: Read appropriate template(s) from references/
- [ ] Step 3: Write extension/agent/{agent_name}.py
- [ ] Step 4: Write extension/prompt/{agent_name}.html (tool-calling only)
- [ ] Step 5: Write configs/agents/{agent_name}.py
- [ ] Step 6: Verify syntax with py_compile
- [ ] Step 7: Call done_tool with type and file paths in reasoning
```

## Output Template

```
Generated agent: {agent_name}
Type: {tool_calling | workflow}
Python: extension/agent/{agent_name}.py
Prompt: extension/prompt/{agent_name}.html (if tool-calling)
Config: configs/agents/{agent_name}.py
```
