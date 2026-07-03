---
name: generate_skill_skill
description: Guides an agent through generating a new skill directory with SKILL.md. Use when asked to create a new skill.
version: 1.0.0
type: worker
license: N/A
category: generation
requirements: [cpu]
metadata: {}
---

# Generate Skill Skill

Creates a new skill directory under `extension/skill/` following the project's skill convention.

## Skill Directory Structure

```
extension/skill/{skill_name}/
├── SKILL.md          # REQUIRED — YAML frontmatter + step-by-step instructions
├── resources/        # optional — runtime data files (JSON, MD) loaded by scripts at runtime
├── scripts/          # optional — executable Python scripts invoked via bash_tool
├── references/       # optional — templates, API specs, guides that agents READ
└── examples/         # optional — examples.md with script invocations; only when scripts/ exists
```

**Directory semantics:**
- `resources/` — runtime data only (e.g. JSON config loaded by a script). Never put templates or docs here.
- `scripts/` — Python scripts run via bash_tool. Must be self-contained and referenced in SKILL.md.
- `references/` — read-only material for agents: code templates, format specs, API docs.
- `examples/` — script usage examples (`examples.md`). Create only when `scripts/` exists.

## Instructions

### Step 1: Determine the skill name

Infer a `snake_case` name from the task (e.g. `code_review_skill`).

### Step 2: Read the template

Read `{skill_dir}/references/skill_md_template.md` to understand the required SKILL.md structure.

### Step 3: Write SKILL.md

Write `{project_root}/extension/skill/{skill_name}/SKILL.md`.

Rules:
- Frontmatter must include: `name`, `description`, `version: 1.0.0`, and `type`.
- `type` is one of: `worker` (a single-agent SOP — how ONE agent does a task; visible to sub-agents) or `orchestrator` (an agent-composition recipe — how the MetaAgent fans work across sub-agents; visible only to the MetaAgent). Most skills are `worker`. An `orchestrator` skill must stay agent-agnostic (describe roles, never hardcode agent names).
- `description` must say what the skill does **and when an agent should use it**.
- Instructions must be concrete and actionable — no vague directives.

### Step 4: Add optional subdirectories

Create as needed:
- `resources/` — JSON or MD config/data files the skill references
- `scripts/` — Python helper scripts called via `bash_tool`
- `references/` — detailed reference docs too long for SKILL.md
- `examples/` — script usage examples (`examples.md`); **only create if the skill has scripts/**

### Step 5: Verify YAML frontmatter

Check all required fields are present and valid.

### Step 6: Call done_tool

Include the skill directory path in `reasoning`:
`reasoning: "Generated extension/skill/{skill_name}/. SKILL.md verified."`

## Workflow

```
- [ ] Step 1: Determine skill name
- [ ] Step 2: Read resources/skill_md_template.md
- [ ] Step 3: Write extension/skill/{skill_name}/SKILL.md
- [ ] Step 4: Create resources/ scripts/ references/ examples/ as needed
- [ ] Step 5: Verify YAML frontmatter
- [ ] Step 6: Call done_tool with skill directory path in reasoning
```

## Output Template

```
Generated skill: {skill_name}
Directory: extension/skill/{skill_name}/
Type: worker | orchestrator
Description: {one-line description}
```
