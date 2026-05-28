---
name: generate_skill_skill
description: Guides an agent through generating a new skill directory with SKILL.md. Use when asked to create a new skill.
version: 1.0.0
type: sop
---

# Generate Skill Skill

Creates a new skill directory under `src/skill/extended/` following the project's skill convention.

## Instructions

### Step 1: Determine the skill name

Infer a `snake_case` name from the task (e.g. `code_review_skill`).

### Step 2: Read the template

Read the template at `{skill_dir}/resources/skill_md_template.md` to understand the required SKILL.md structure.

### Step 3: Create the skill directory and SKILL.md

Write `{project_root}/src/skill/extended/{skill_name}/SKILL.md`.

Rules:
- Frontmatter must include: `name`, `description`, `version: 1.0.0`, `type: sop`.
- `description` should say what the skill does **and when an agent should use it**.
- Instructions must be concrete and actionable — no vague directives.
- Include at least one example in the `## Examples` section.

### Step 4: Add optional resources/scripts

If the skill references data files or helper scripts, create them under `resources/` or `scripts/`.

### Step 5: Verify YAML frontmatter

Check frontmatter is valid YAML: all required fields present, no syntax errors.

### Step 6: Call done_tool

Include the skill directory path in `reasoning`:
`reasoning: "Generated src/skill/extended/{skill_name}/. SKILL.md verified."`

## Workflow

```
- [ ] Step 1: Determine skill name
- [ ] Step 2: Read resources/skill_md_template.md
- [ ] Step 3: Write src/skill/extended/{skill_name}/SKILL.md
- [ ] Step 4: Create resources/scripts if needed
- [ ] Step 5: Verify YAML frontmatter
- [ ] Step 6: Call done_tool with skill directory path in reasoning
```

## Output Template

```
Generated skill: {skill_name}
Directory: src/skill/extended/{skill_name}/
Type: sop
Description: {one-line description}
```
