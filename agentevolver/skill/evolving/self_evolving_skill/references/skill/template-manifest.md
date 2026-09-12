---
name: my_skill
description: What this skill does and the concrete contexts where its method helps. Include relevant boundaries so unrelated tasks do not trigger it.
version: 1.0.0
type: worker
requirements: [cpu]
metadata: {}
---

<!--
TEMPLATE — SKILL.md. Copy to `{extension_root}/skill/{name}/SKILL.md`, fill the frontmatter
and body. `type` is one (or more) of: `worker` (an SOP for one agent, visible to
sub-agents) / `orchestrator` (a capability coordination method). Add optional
subdirectories only when needed: scripts/ (Python run via bash_tool), references/
(docs the agent reads), resources/ (runtime data), examples/ (examples.md; only if
scripts/ exists). Keep this body under ~500 lines; push detail into references/.
-->

# Skill Title

Brief overview: what this skill achieves and its primary use case. Explain the WHY
so the agent understands intent rather than following brittle rules.

## Instructions

### Step 1: [First action]
Concrete, imperative description of what to do, including any conditions/decisions.

### Step 2: [Second action]
What to do and how to verify it worked.

### Step 3: Verify and report
Verify this method's result and return its evidence to the consuming task. The agent decides
when the complete user task is done; finishing this skill alone does not end that task.

## Output format
(If the skill produces a structured artifact, show the exact template here.)

```
[Concrete output template]
```

## Examples
(Optional but valuable — show 1-2 realistic input → output pairs.)
