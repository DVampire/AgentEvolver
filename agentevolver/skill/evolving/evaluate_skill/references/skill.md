# Evaluating a skill

## What it is

A skill is a directory, `{extension_root}/skill/{name}/`, holding a required `SKILL.md`
and optional `scripts/`, `references/`, `resources/` and `examples/`.

**The contract**: the frontmatter — `name`, `description`, `version`, `type` (`worker` and/or
`orchestrator`) — and above all the `description`, which decides whether the skill is ever
triggered at all.

## Evaluating a skill

Goal: measure whether the skill actually helps, and how good its outputs are — empirically, not just by reading it.

### Static check (always do this)

Read the SKILL.md and score it on: instruction clarity, completeness, structure/format, and whether the description states both what-it-does and when-to-use. Run `scripts/skill/quick_validate.py` for the structural pass (frontmatter present, required fields, sane layout). Use `inspect_tool` (`capability_type="skill"`) to confirm the skill is registered and to get its directory.

### Empirical check (with-skill vs baseline)

The heart of quantitative evaluation is: does the skill help versus not having it? In this framework there is no `claude -p` subprocess and no browser viewer — **MetaAgent runs the comparison by dispatching agents** (see Orchestration). Concretely, for each test prompt:

- **with-skill run**: use an available, authorized bounded consumer with the target skill
  made available (`skill_allowlist` pinned to `[target_skill]`). Do not assume a particular
  agent such as `general_agent` is mounted.
- **baseline run**: use an equivalent fresh consumer on the same prompt with the prior
  skill version or without the new skill (`skill_allowlist: []`). Keep the model, task,
  other capabilities and budget comparable; never pretend previously learned instructions
  have been removed from a continuing conversation.

For a small change, one representative comparison and one independent reuse or regression
case are sufficient when they cover the affected behavior. Larger changes require broader
coverage. If the required consumer or permissions are unavailable, report the missing evidence
as inconclusive; do not fabricate a comparison from reading the skill.

Organize outputs under `{skill_dir}/evals/iteration-N/eval-<id>/{with_skill,baseline}/`. Then grade.

### Grading

For each test case, evaluate the outputs against the assertions (objectively verifiable checks with descriptive names). Where an assertion is programmatically checkable, write and run a small script rather than eyeballing it — faster, reliable, reusable. Save results to `grading.json` per run (use fields `text`, `passed`, `evidence`). Aggregate into a benchmark:
```bash
python {skill_dir}/scripts/skill/aggregate_benchmark.py {skill_dir}/evals/iteration-N --skill-name {name}
```
This produces pass_rate / time / tokens per configuration (with-skill vs baseline), with the delta — the objective signal for whether the skill helps. See `references/skill/schemas.md` for the exact JSON the aggregator expects.

Produce a scored report: per-dimension scores (static) + the with-skill/baseline benchmark (empirical) + concrete improvement suggestions.

---
