# Writing a skill

## What it is

A skill is a directory, `{extension_root}/skill/{name}/`, holding a required `SKILL.md`
and optional `scripts/`, `references/`, `resources/` and `examples/`.

**The contract**: the frontmatter — `name`, `description`, `version`, `type` (`worker` and/or
`orchestrator`) — and above all the `description`, which decides whether the skill is ever
triggered at all.

## Framework conventions (read once)

- Skills live in `{extension_root}/skill/{skill_name}/` (generated skills) or `agentevolver/skill/default/{skill_name}/` (defaults). Use `snake_case` names ending in `_skill`.
- A skill directory:
  ```
  {skill_name}/
  ├── SKILL.md        # REQUIRED — YAML frontmatter + instructions
  ├── scripts/          # optional — Python run via bash_tool (deterministic/repetitive work)
  ├── references/       # optional — docs the agent READs as needed
  ├── resources/      # optional — runtime data files loaded by scripts
  └── examples/       # optional — examples.md; only when scripts/ exists
  ```
- **Registration is automatic via a hook**: after you finish writing/editing the files, include the skill directory path in your `done_tool` reasoning — the registration hook picks it up. Do NOT package a `.skill` file; this framework registers from the directory.
- **Frontmatter** must include `name`, `description`, `version`, and `type`. `type` is one or more labels: `worker` (an SOP for one agent — visible to sub-agents) and/or `orchestrator` (a composition recipe for MetaAgent — how to fan work across sub-agents). Most skills are `worker`.

---

## Creating a skill

### Capture intent

Start by understanding the intent. The task (or conversation history) may already contain the workflow to capture — the tools used, the sequence of steps, the input/output formats. Extract those first. Pin down:

1. What should this skill enable an agent to do?
2. When should it trigger? (what user phrasings/contexts)
3. What's the expected output format?
4. Are there objectively verifiable outputs (file transforms, data extraction, code generation, fixed workflow steps)? Those benefit from test cases. Subjective outputs (writing style, design) usually don't.

### Write the SKILL.md

**Start from the template**: read `references/skill/skill_md_template.md`, copy it, and fill it in.

Fill in these components:

- **name**: the skill identifier (`snake_case`, ends in `_skill`).
- **description**: the primary triggering mechanism — include both **what** the skill does AND the specific **when to use** contexts. All "when to use" info goes here, not in the body. Agents tend to *under*-trigger skills, so make the description a little **pushy**: instead of "How to build a dashboard", write "How to build a dashboard. Use this whenever the user mentions dashboards, data visualization, or wants to display any kind of data, even if they don't explicitly say 'dashboard.'"
- **version**, **type**, **requirements**, **metadata**: per the conventions above.
- **the body** — the actual instructions.

### Skill writing guide

#### Progressive disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) — always in context (~100 words).
2. **SKILL.md body** — in context whenever the skill triggers (<500 lines ideal).
3. **Bundled resources** (scripts/, references/) — loaded/executed only as needed (unlimited; scripts run without loading into context).

Keep SKILL.md under ~500 lines. If you approach that, add a layer of hierarchy: move detail into `references/` and point to it clearly from SKILL.md ("read `references/x.md` when you need Y"). For large reference files (>300 lines), include a table of contents.

**Domain organization**: when a skill supports multiple variants, organize by variant so the agent reads only the relevant reference:
```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/{aws,gcp,azure}.md
```

#### Principle of lack of surprise

Skills must not contain malware, exploit code, or anything that could compromise security. A skill's contents should not surprise the user relative to its stated intent. Don't create misleading skills or skills designed to facilitate unauthorized access or data exfiltration.

#### Writing patterns

Prefer the imperative form. Define output formats explicitly:
```markdown

## Report structure
ALWAYS use this exact template:
# [Title]

## Executive summary

## Key findings
```
Include examples where useful:
```markdown

## Commit message format
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

#### Writing style

Explain **why** things matter rather than piling on heavy-handed MUSTs. Today's models have good theory of mind — given the reasoning, they go beyond rote instructions. If you catch yourself writing ALWAYS/NEVER in all caps or rigid structures, that's a yellow flag: reframe and explain the reasoning instead. Write a draft, then reread it with fresh eyes and improve it. Keep it general, not overfit to one example.

### Test cases

After the draft, write 2-3 realistic test prompts — the kind of thing a real user would actually say. Save them to `{skill_dir}/evals/evals.json` (prompts only; assertions come later during evaluation):

```json
{
  "skill_name": "example_skill",
  "evals": [
    {"id": 1, "prompt": "User's task prompt", "expected_output": "Description of expected result", "files": []}
  ]
}
```

See `references/skill/schemas.md` for the full schema (including the `assertions` field added during evaluation). You can quickly sanity-check a draft's structure with:
```bash
python {skill_dir}/scripts/skill/quick_validate.py {path_to_skill_dir}
```

When the files are written and validated, put the skill directory path in your `done_tool` reasoning so the registration hook installs it.

---

## Description optimization (triggering)

The `description` frontmatter is the primary mechanism that decides whether an agent invokes a skill. After creating or improving a skill, it's worth tuning the description for triggering accuracy.

### How triggering works

Skills appear in an agent's skill_context as name + description; the agent decides whether to consult a skill from that alone. Agents only reach for skills on tasks they can't trivially handle themselves — a simple one-step query may not trigger a skill even with a perfect description, because the agent just does it directly. So test queries must be **substantive** enough that an agent would actually benefit from the skill. Simple queries like "read file X" are poor test cases.

### Measuring triggering (general_agent probe)

We do NOT ship a special trigger tool. Triggering is measured with a `general_agent` probe dispatched by MetaAgent:

- Build a set of ~20 realistic labeled queries — a mix of should-trigger (8-10) and should-not-trigger (8-10). The most valuable negatives are **near-misses**: queries that share keywords with the skill but actually need something else. Don't make negatives obviously irrelevant ("write a fibonacci function" as a negative for a PDF skill tests nothing).
- **Judge mode (default, cheap)**: dispatch a `general_agent` with a task that gives it the target skill's name+description alongside a few distractor skills and the labeled queries, and asks it to decide, per query, which skill it would invoke — then report per-query hits/misses/false-triggers and overall accuracy. This mirrors the real selection decision; use the misses to revise the description and re-run.
- *(Higher-fidelity alternative, optional)*: dispatch `general_agent` on each real query with the skill in its `skill_allowlist` and observe whether it actually invokes the target skill. This measures real triggering but costs a full run per query and needs the invocation read from the run's trace.

Revise the description to fix under-triggering (misses) and over-triggering (false triggers), then re-run until accuracy is good. Show the before/after description and the scores.

---
