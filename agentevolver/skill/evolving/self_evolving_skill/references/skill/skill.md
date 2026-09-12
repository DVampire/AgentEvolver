# Skill

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

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
- **Registration is a call you make**: after you finish writing/editing the files, call `adoption_tool` with `action="register"`, `module="skill"`, the skill name, and the skill directory as `artifact_path`. Do NOT package a `.skill` file; this framework registers from the directory.
- **Frontmatter** must include `name`, `description`, `version`, and `type`. Use `worker`
  for a method executed by one agent and/or `orchestrator` for capability coordination.
  These labels do not require a particular MetaAgent or authorize sub-agent dispatch.

---

## Writing a new one

### Creating a skill

#### Capture intent

Start by understanding the intent. The task (or conversation history) may already contain the workflow to capture — the tools used, the sequence of steps, the input/output formats. Extract those first. Pin down:

1. What should this skill enable an agent to do?
2. When should it trigger? (what user phrasings/contexts)
3. What's the expected output format?
4. What evidence can check the outputs? Use executable checks for transforms or extraction,
   and explicit comparisons of actual outputs for qualitative writing/design claims.

#### Write the SKILL.md

**Start from the template**: read `references/skill/template-manifest.md`, copy it, and fill it in.

Fill in these components:

- **name**: the skill identifier (`snake_case`, ends in `_skill`).
- **description**: state what the skill does and the concrete contexts where its method
  helps. Include useful boundaries; do not trigger from broad keywords or unrelated tasks.
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

Design representative prompts and independent cases for the affected method, following the
common evaluation scope. Reusable fixtures may be bundled in the candidate before registration;
store subsequent evaluation results in the work record without changing the evaluated source.
The optional benchmark helpers accept an `evals.json` with this shape:

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

When the files are written and validated, register it: `adoption_tool` with `action="register"`, `module="skill"` and the skill directory as `artifact_path`.

---

### Description optimization (triggering)

The `description` frontmatter is the primary mechanism that decides whether an agent invokes a skill. After creating or improving a skill, it's worth tuning the description for triggering accuracy.

#### How triggering works

Skills appear in an agent's skill_context as name + description; the agent decides whether to consult a skill from that alone. Agents only reach for skills on tasks they can't trivially handle themselves — a simple one-step query may not trigger a skill even with a perfect description, because the agent just does it directly. So test queries must be **substantive** enough that an agent would actually benefit from the skill. Simple queries like "read file X" are poor test cases.

#### Measuring triggering

For a description change, compare representative should-trigger queries and close near-miss
negatives. Use an available authorized fresh consumer for a selection probe: present only the
name/description and competing capabilities, then record its choice. This measures selection
judgment, not actual method quality. A full task run with observed invocation provides stronger
triggering evidence when warranted by the claim and budget. Do not assume `general_agent` is
mounted, require a fixed query count, or dispatch when the task disables children.

Report false triggers and missed opportunities, preserving examples for the next revision.
Keep triggering evaluation distinct from the method's executed outcome comparison below.

## Improving an existing one

This is the heart of the loop. Given evaluation results (and any concrete complaints about specific test cases), make the skill better.

### How to think about improvements

1. **Generalize from the signal.** A skill is meant to be used across many prompts, not just the handful you're iterating on. Don't put in fiddly overfit changes or oppressively constrictive MUSTs to patch one example. If some issue is stubborn, try a different framing or metaphor — it's cheap to try and you may land on something much better.
2. **Keep it lean.** Remove things that aren't pulling their weight. Read the *transcripts*, not just the final outputs — if the skill is making the agent waste steps on something unproductive, cut the part causing it and see what happens.
3. **Explain the why.** Try hard to explain the reasoning behind everything you ask the agent to do. Even when the signal is terse, understand what the user actually needs and transmit that understanding into the instructions. All-caps ALWAYS/NEVER and rigid structures are a yellow flag — reframe and explain instead.
4. **Look for repeated work across test cases.** If every test run independently wrote a similar helper script or took the same multi-step approach, that's a strong signal the skill should bundle that script. Write it once, put it in the new skill's `scripts/`, and have the skill use it — saving every future invocation from reinventing the wheel.

Take your time here — thinking time is not the blocker. Draft a revision, reread it fresh, improve.

### The iteration loop

Use the shared author → register → evaluate → repair/adopt loop. Skill-specific changes may
include its description, instructions, references or supporting scripts. Keep evaluation
outputs outside the registered skill directory so collection does not mutate the candidate.
Register the staged directory, including `SKILL.md` and dependencies; use the returned version.
Preserve prior comparisons and rerun affected cases and regressions after a revision. Stop an
unproductive experiment according to its budget; do not keep expanding a benchmark indefinitely.

## Evaluating one

Goal: measure whether the skill actually helps, and how good its outputs are — empirically, not just by reading it.

### Static check (always do this)

Read the SKILL.md and score it on: instruction clarity, completeness, structure/format, and whether the description states both what-it-does and when-to-use. Run `scripts/skill/validate.py` for the structural pass (frontmatter present, required fields, sane layout). Use `inspect_tool` (`capability_type="skill"`) to confirm the skill is registered and to get its directory.

### Empirical check (with-skill vs baseline)

Invoke the registered skill and execute its method. For a procedural method with objective
outputs, the current agent can compare concrete baseline and candidate operations and exercise
a distinct branch/reuse input. Do not claim this proves independent model behavior.

When the claim concerns reasoning or subjective output quality, use equivalent fresh consumers:
- Candidate: make the exact skill version available and verify it was used.
- Baseline: use the prior version or no new skill, keeping task, model, other capabilities
  and budget comparable. Preserve baseline results before installing the candidate, or use
  supported isolated version selection; do not switch a shared active version under a consumer.

Follow the common comparison scope and permission limits. A missing required consumer leaves
that evidence inconclusive. Reading instructions alone, replaying an answer already seen, or
pretending to unlearn the skill in the same conversation cannot establish improvement.

If using the optional aggregator, organize results under a work-record directory as
`iteration-N/eval-<id>/{with_skill,baseline}/`, then grade.

### Grading

For each test case, evaluate the outputs against the assertions (objectively verifiable checks with descriptive names). Where an assertion is programmatically checkable, write and run a small script rather than eyeballing it — faster, reliable, reusable. Save results to `grading.json` per run (use fields `text`, `passed`, `evidence`). Aggregate into a benchmark:
```bash
python {skill_dir}/scripts/skill/benchmark.py {evaluation_root}/iteration-N --skill-name {name}
```
This produces pass_rate / time / tokens per configuration (with-skill vs baseline), with the delta — the objective signal for whether the skill helps. See `references/skill/schemas.md` for the exact JSON the aggregator expects.

Record concrete static findings and executed outcome comparisons through the shared decision
contract. Optional benchmark aggregates and scores support that evidence; they do not adopt
the skill or replace actual consumer use.

---
