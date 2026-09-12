# Agent

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

An agent is a Python file under `{extension_root}/agent/`, usually paired with an HTML
prompt at `{extension_root}/prompt/{name}.html`. Changing only the prompt is a complete, valid
change.

**The contract**: the class stays thin — it inherits the standard loop rather than
reimplementing it — and the prompt puts `<capability-context>` before `<agent-context>` in the
user turn, because nothing after the state can be cached.

An agent's class and its prompt evolve together. An agent in this framework is a thin Python class over the shared base `Agent`, usually paired with an HTML prompt (for tool-calling agents) and a config.

At a high level, building a good agent is a loop: decide what it should do → pick the agent type → write a thin class + a focused prompt from the templates → run it on realistic tasks → evaluate → improve the prompt/class → repeat.

## Layout

An agent has up to three files:
- `{extension_root}/agent/{name}.py` — the Python class (REQUIRED).
- `{extension_root}/prompt/{name}.html` — the HTML prompt (REQUIRED for tool-calling agents; procedural agents omit it).
- `{extension_root}/configs/agents/{name}.py` — the config dict.

**Registration is a call you make**: after writing the files, call `adoption_tool` with `action="register"`, `module="agent"`, the agent name, and the Python file as `artifact_path`.

---

## Writing a new one

### Reference templates — read before writing

Start from the bundled templates instead of writing from scratch:
- `references/agent/template.py` — a thin tool-calling agent class (the common case).
- `references/agent/template-procedural.py` — a deterministic, code-driven agent (no LLM loop, no prompt).
- `references/agent/template-prompt.html` — the HTML prompt skeleton for a tool-calling agent.

Read the relevant template(s) first, copy, then adapt. They already encode the current architecture and the template-variable contract.

### Choosing the agent type

- **Tool-calling agent** (default): reasons and acts step by step, choosing tools/skills dynamically each step. Use it for open-ended or multi-step tasks. It has a Python class + an HTML prompt, and it **inherits** the base loop. → `template.py` + `template-prompt.html`.
- **Procedural agent**: a fixed, deterministic pipeline (read → process → report) expressed in code with direct tool calls — no step-by-step LLM planning, no prompt. It overrides `__call__` and calls no model; the kernel does not care what is inside. → `template-procedural.py`.

When unsure, prefer a tool-calling agent — it's the more general, more capable form.

---

### Creating an agent

#### The class is THIN — inherit, don't reinvent

The base `Agent` owns the loop (`__call__` → `think` → `act`), prompt assembly (`agent/context/`), the executor and the router. A well-formed tool-calling agent **inherits all of it** and supplies only field declarations:
- identity (`name`, `description`, `metadata`, `enable_evolving`),
- `prompt_name`, which must match the HTML prompt's `<meta name="name">`,
- `max_step`, if the default of 20 is wrong for this work.

**Write no `__init__` and no `__call__`.** Prefer field declarations for a standard tool-calling actor. The base takes `base_dir` and forwards keyword arguments to pydantic, so a hand-written `__init__` that defaults fields to `None` and passes them through makes the agent fail to construct, which is what an earlier version of the template did.

**Do NOT override** `think`, `act` or anything in `agent/context/` unless the agent genuinely needs bespoke behaviour — reviewers treat unnecessary overrides as a defect. The seams that exist for real needs are `prompt_modules`, `project_context`, `working_memory`, `completion_blocker`, `finalize`, and the runtime phases `on_start` / `on_land` / `on_exit` / `on_suspend` / `on_resume`. Behavioral guidance belongs in the prompt or an applicable skill; do not add runtime guards merely to remind the model.

Steps:
1. Read `template.py`, copy it to `{extension_root}/agent/{name}.py`, rename the class, and fill `name` / `description` (state what it does AND when to use it) / `prompt_name`.
2. Write the HTML prompt (next section).
3. `python -m py_compile /abs/path/{name}.py`; then register it: `adoption_tool` with `action="register"`, `module="agent"` and the `.py` path as `artifact_path`.

#### Writing the HTML prompt (this is where agent quality lives)

Copy `template-prompt.html` to `{extension_root}/prompt/{name}.html`, set `<meta name="name">` to the agent's name, and fill each block. The prompt is the agent's brain — treat it with the same care as a skill.

**Structure (do not break it):**
- **system**: `profile`, `language-settings`, `project`, `input-rules`, `constraint-rules`, `task-rules`, `context-rules`, `response-protocol`.
- **user**: stable-to-live ordering — `<capability-context>` (tools, skills, connectors,
  plugins, workflows, sub-agents), optional `<environment-context>`, then `<agent-context>`
  (task, inherited context, plan, constraints, step info, environment state, workspace,
  errors). The CSS, renderer, context builder, and prompt cache depend on this layout.
  - **The order is not cosmetic.** Stable catalogs precede live state so a changing step does
    not invalidate the reusable prefix. The context assembler then sends callable catalogs as
    provider-native tool definitions, keeps task/inherited context as the first user anchor,
    carries old work in one memory checkpoint, and preserves recent assistant/tool turns.

**What each block is for** (fill the agent-specific ones; keep the shared ones roughly as the template has them):
- `profile` *(agent-specific)* — who the agent is and its core behavior; explain the WHY, not just rules.
- `language-settings` *(shared)* — working language and "reply in the request's language".
- `project` *(agent-specific)* — the paths this agent may read/write and its permission posture (read-only vs edit). This is a real guardrail — say exactly where it may write.
- `input-rules` *(shared with small agent-specific additions)* — native capability schemas are
  authoritative; explain the stable catalogs, live state, and separate native history.
- `constraint-rules` *(shared)* — the resource-budget / urgency-tier contract (NORMAL / TIGHT / CRITICAL).
- `task-rules` *(agent-specific)* — the agent's objective and when to call `done_tool`.
- `context-rules` *(shared)* — how checkpoints plus recent native turns carry history, and that
  the agent calls only mounted native capabilities.
- `response-protocol` *(shared)* — that it acts by **calling tools natively** (not by emitting a JSON plan), and signals completion only via `done_tool`.
- `capability-context` + `environment-context` + `agent-context` *(shared frame)* — the
  stable catalogs and live-state slots; only the template variables below go here.

> **Shared blocks & modules.** The built-in default agents in `agentevolver/prompt/default/` factor the shared blocks (`language-settings`, `constraint-rules`, `context-rules`, `response-protocol`, `agent-context`) into `agentevolver/prompt/module/*.html`, referenced with `<module src="../module/NAME.html"></module>` (the server inlines them into the message; `prompt.js` inlines them for browser viewing). **Generated agents keep these blocks inline** — do NOT use `<module src>` in an `{extension_root}/prompt/` file: module `src` is resolved relative to the prompt file, so `../module/...` only exists under `agentevolver/prompt/default/` and would fail to load from `{extension_root}/prompt/`.

**Template-variable contract** — use only variables the base context builder provides,
spelled exactly: `{{ task }}`, `{{ inherited_context }}`, `{{ plan }}`,
`{{ constraint_text }}`, `{{ step_info }}`, `{{ environment_state }}`,
`{{ workspace }}`, `{{ errors }}`, `{{ available_tools }}`, `{{ available_skills }}`,
`{{ available_connectors }}`, `{{ available_plugins }}`, `{{ available_workflows }}`,
`{{ available_agents }}`, `{{ environment_context }}`, plus system-side path/runtime
variables shown in the template. Memory is injected as native conversation history; do not
invent a new `<memory>` slot. An unknown or misspelled variable silently drops context.

**Response contract — native tool calls (NOT a JSON plan)**: the base loop turns the agent's capabilities into native tools and reads the model's `tool_calls` each step; it does NOT parse a JSON `plan`/`output-schema` from the text. So `response-protocol` must tell the agent to **act by calling tools** and to finish only by calling `done_tool`. (Older prompts used a `plan`/`output-schema` JSON contract — that is obsolete; do not reintroduce it.)

**Writing principles (borrowed from good skill authoring):**
- Prefer the imperative. Define concrete rules, not vague directives.
- **Explain the WHY.** Modern models have good theory of mind — when you explain why a rule matters, they generalize instead of following it brittly. All-caps ALWAYS/NEVER and rigid structures are a yellow flag: reframe as reasoning.
- Keep it lean — every line should earn its place. Remove instructions that don't change behavior.
- Make the `profile` and `task-rules` say clearly what the agent is for and how it should approach work.
- Draft it, then reread with fresh eyes and cut/clarify.

---

## Improving an existing one

Most agent improvement is **prompt improvement**. Inspect the actual Agent and its supporting
prompt together. Diagnose the behavior from a real transcript; do not infer mutability from
whether an agent is built-in or generated. Follow the common optimize/alternative path.

- Read the Python and HTML before editing. Decide whether the fix is in the **prompt** (behavior, rules, reasoning — most common) or the **class** (a real code bug).
- Make the smallest correct change. Preserve `@AGENT.register_module`, `name`, and the prompt's `agent-context` structure and template variables.
- A `<module src="...">` block can be shared by several agents. Keep a scoped candidate's
  prompt self-contained according to the template; do not edit shared package modules to
  change one generated agent.
- Keep the class a declaration — prefer fixing the prompt over adding loop overrides. An actor that overrides `think`, `act`, or anything in `agent/context/` is a candidate to remove: the loop, the assembler and the executor are the same for every agent, so an override is either a genuine new behaviour or an accident. Behavioral guidance belongs in the prompt or an applicable skill; do not add runtime guards merely to remind the model.
- Apply the prompt writing principles above: explain the why, cut dead instructions, sharpen the rules the agent kept getting wrong.
- Verify construction and prompt rendering as well as Python compilation. Register the
  staged Agent `.py` together with its supporting prompt in the expected layout, even when
  only the prompt changed; inspect the returned version before evaluation.

---

## Evaluating one

Call `inspect_tool` (`capability_type="agent"`) on the target for its registry facts (registered / instantiated / version / file paths). Check the type-specific requirements:

1. **Interface Compliance** — `@AGENT.register_module`, inherits `Agent`, has `name`/`description`/`metadata`/`enable_evolving`; **cleanly inherits the base loop**. Tool-calling agents must not override `__call__`; an agent whose main function is code overrides `__call__` and calls no model.
2. **Code Quality** — clean, valid, no dead code; lifecycle hooks come from the inherited loop, not re-implemented.
3. **Prompt Quality** — HTML present (tool-calling) with the required sections, the container-vs-sibling `agent-context` layout, correct template variables, and a `response-protocol` that drives **native tool calls** (no obsolete JSON `plan`/`output-schema`). Auto-pass for procedural agents (no prompt).
4. **Integration** — `inspect_tool` (`capability_type="agent"`) shows Registered + Instantiated.
5. **Task Execution** — a valid execution path: a tool-calling agent with a valid `prompt_name` inheriting the loop, or a code-driven agent overriding `__call__`.

Exercise the registered Agent in a supported fresh bounded consumer and collect its actual
output, trace and cost. Compare with an equivalent baseline and an independent case, keeping
model, task, capabilities and budgets comparable. Registration cannot replace the current
running agent. A missing dispatch/handoff facility leaves behavioral evidence inconclusive;
a valid Python class and rendered prompt alone cannot establish improved reasoning.

---
