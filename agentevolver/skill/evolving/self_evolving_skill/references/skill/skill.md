# Skill

The full lifecycle of one component type: what a skill is, how to write one, how to
change one, and how to judge one. The contract below holds for all three.

## What it is

A skill is a directory, `{extension_root}/skill/{name}/`, holding a required `SKILL.md`
and optional `scripts/`, `references/`, `resources/` and `examples/`.

**The contract**: the frontmatter — `name`, `description`, `version`, `type` (`worker` and/or
`orchestrator`) — and above all the `description`, which decides whether the skill is ever
triggered at all.

## Layout

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

## Writing a new one

### Creating a skill

#### Capture intent

Start by understanding the intent. The task (or conversation history) may already contain the workflow to capture — the tools used, the sequence of steps, the input/output formats. Extract those first. Pin down:

1. What should this skill enable an agent to do?
2. When should it trigger? (what user phrasings/contexts)
3. What's the expected output format?
4. Are there objectively verifiable outputs (file transforms, data extraction, code generation, fixed workflow steps)? Those benefit from test cases. Subjective outputs (writing style, design) usually don't.

#### Write the SKILL.md

**Start from the template**: read `references/skill/template-manifest.md`, copy it, and fill it in.

Fill in these components:

- **name**: the skill identifier (`snake_case`, ends in `_skill`).
- **description**: the primary triggering mechanism — include both **what** the skill does AND the specific **when to use** contexts. All "when to use" info goes here, not in the body. Agents tend to *under*-trigger skills, so make the description a little **pushy**: instead of "How to build a dashboard", write "How to build a dashboard. Use this whenever the user mentions dashboards, data visualization, or wants to display any kind of data, even if they don't explicitly say 'dashboard.'"
- **version**, **type**, **requirements**, **metadata**: per the conventions above.
- **the body** — the actual instructions.

#### Skill writing guide

##### Progressive disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) — always in context (~100 words).
2. **SKILL.md body** — in context whenever the skill triggers (<500 lines ideal).
3. **Bundled resources** (scripts/, references/) — loaded/executed only as needed (unlimited; scripts run without loading into context).

Keep SKILL.md under ~500 lines. If you approach that, add a layer of hierarchy: move detail into `references/` and point to it clearly from SKILL.md ("read the deployment reference when you need to ship"). For large reference files (>300 lines), include a table of contents.

**Domain organization**: when a skill supports multiple variants, organize by variant so the agent reads only the relevant reference:
```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/{aws,gcp,azure}.md
```

##### Principle of lack of surprise

Skills must not contain malware, exploit code, or anything that could compromise security. A skill's contents should not surprise the user relative to its stated intent. Don't create misleading skills or skills designed to facilitate unauthorized access or data exfiltration.

##### Writing patterns

Prefer the imperative form. Define output formats explicitly:
```markdown

### Report structure

ALWAYS use this exact template:
# [Title]

### Executive summary



### Key findings

```
Include examples where useful:
```markdown

### Commit message format

Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

##### Writing style

Explain **why** things matter rather than piling on heavy-handed MUSTs. Today's models have good theory of mind — given the reasoning, they go beyond rote instructions. If you catch yourself writing ALWAYS/NEVER in all caps or rigid structures, that's a yellow flag: reframe and explain the reasoning instead. Write a draft, then reread it with fresh eyes and improve it. Keep it general, not overfit to one example.

#### Test cases

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
python {skill_dir}/scripts/skill/validate.py {path_to_skill_dir}
```

When the files are written and validated, put the skill directory path in your `done_tool` reasoning so the registration hook installs it.

---

### Description optimization (triggering)

The `description` frontmatter is the primary mechanism that decides whether an agent invokes a skill. After creating or improving a skill, it's worth tuning the description for triggering accuracy.

#### How triggering works

Skills appear in an agent's skill_context as name + description; the agent decides whether to consult a skill from that alone. Agents only reach for skills on tasks they can't trivially handle themselves — a simple one-step query may not trigger a skill even with a perfect description, because the agent just does it directly. So test queries must be **substantive** enough that an agent would actually benefit from the skill. Simple queries like "read file X" are poor test cases.

#### Measuring triggering (general_agent probe)

We do NOT ship a special trigger tool. Triggering is measured with a `general_agent` probe dispatched by MetaAgent:

- Build a set of ~20 realistic labeled queries — a mix of should-trigger (8-10) and should-not-trigger (8-10). The most valuable negatives are **near-misses**: queries that share keywords with the skill but actually need something else. Don't make negatives obviously irrelevant ("write a fibonacci function" as a negative for a PDF skill tests nothing).
- **Judge mode (default, cheap)**: dispatch a `general_agent` with a task that gives it the target skill's name+description alongside a few distractor skills and the labeled queries, and asks it to decide, per query, which skill it would invoke — then report per-query hits/misses/false-triggers and overall accuracy. This mirrors the real selection decision; use the misses to revise the description and re-run.
- *(Higher-fidelity alternative, optional)*: dispatch `general_agent` on each real query with the skill in its `skill_allowlist` and observe whether it actually invokes the target skill. This measures real triggering but costs a full run per query and needs the invocation read from the run's trace.

Revise the description to fix under-triggering (misses) and over-triggering (false triggers), then re-run until accuracy is good. Show the before/after description and the scores.

---

## Improving an existing one

This is the heart of the loop. Given evaluation results (and any concrete complaints about specific test cases), make the skill better.

### How to think about improvements

1. **Generalize from the signal.** A skill is meant to be used across many prompts, not just the handful you're iterating on. Don't put in fiddly overfit changes or oppressively constrictive MUSTs to patch one example. If some issue is stubborn, try a different framing or metaphor — it's cheap to try and you may land on something much better.
2. **Keep it lean.** Remove things that aren't pulling their weight. Read the *transcripts*, not just the final outputs — if the skill is making the agent waste steps on something unproductive, cut the part causing it and see what happens.
3. **Explain the why.** Try hard to explain the reasoning behind everything you ask the agent to do. Even when the signal is terse, understand what the user actually needs and transmit that understanding into the instructions. All-caps ALWAYS/NEVER and rigid structures are a yellow flag — reframe and explain instead.
4. **Look for repeated work across test cases.** If every test run independently wrote a similar helper script or took the same multi-step approach, that's a strong signal the skill should bundle that script. Write it once, put it in the new skill's `scripts/`, and have the skill use it — saving every future invocation from reinventing the wheel.

Take your time here — thinking time is not the blocker. Draft a revision, reread it fresh, improve.

### The iteration loop

1. Apply the improvements to the skill files.
2. Re-run all test cases into a new `iteration-<N+1>/` directory, including the baseline. (For a *new* skill the baseline is always no-skill; for an *existing* skill, the baseline can be the original version — snapshot it before editing.)
3. Re-grade and re-aggregate; compare against the previous iteration.
4. Repeat until the outputs are good, the benchmark stops improving, or you've stopped making meaningful progress.

When re-registering an edited skill, put the edited SKILL.md path in your `done_tool` reasoning so the hook reloads it.

---

## Evaluating one

Goal: measure whether the skill actually helps, and how good its outputs are — empirically, not just by reading it.

### Static check (always do this)

Read the SKILL.md and score it on: instruction clarity, completeness, structure/format, and whether the description states both what-it-does and when-to-use. Run `scripts/skill/validate.py` for the structural pass (frontmatter present, required fields, sane layout). Use `inspect_tool` (`capability_type="skill"`) to confirm the skill is registered and to get its directory.

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
python {skill_dir}/scripts/skill/benchmark.py {skill_dir}/evals/iteration-N --skill-name {name}
```
This produces pass_rate / time / tokens per configuration (with-skill vs baseline), with the delta — the objective signal for whether the skill helps. See `references/skill/schemas.md` for the exact JSON the aggregator expects.

Produce a scored report: per-dimension scores (static) + the with-skill/baseline benchmark (empirical) + concrete improvement suggestions.

---
