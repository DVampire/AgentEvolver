# Writing an agent

## What it is

An agent is a Python file under `{extension_root}/agent/`, usually paired with an HTML
prompt at `{extension_root}/prompt/{name}.html`. Changing only the prompt is a complete, valid
change.

**The contract**: the class stays thin — it inherits the standard loop rather than
reimplementing it — and the prompt puts `<capability-context>` before `<agent-context>` in the
user turn, because nothing after the state can be cached.

A single skill for the full lifecycle of **agents**: creating new ones, improving existing ones (class *and* prompt), and evaluating their quality. An agent in this framework is a thin Python class over the shared base `Agent`, usually paired with an HTML prompt (for tool-calling agents) and a config.

At a high level, building a good agent is a loop: decide what it should do → pick the agent type → write a thin class + a focused prompt from the templates → run it on realistic tasks → evaluate → improve the prompt/class → repeat.

## Reference templates — read before writing

Start from the bundled templates instead of writing from scratch:
- `references/agent/tool_calling_agent_template.py` — a thin tool-calling agent class (the common case).
- `references/agent/procedural_agent_template.py` — a deterministic, code-driven agent (no LLM loop, no prompt).
- `references/agent/html_prompt_template.html` — the HTML prompt skeleton for a tool-calling agent.

Read the relevant template(s) first, copy, then adapt. They already encode the current architecture and the template-variable contract.

## Framework conventions (read once)

An agent has up to three files:
- `{extension_root}/agent/{name}.py` — the Python class (REQUIRED).
- `{extension_root}/prompt/{name}.html` — the HTML prompt (REQUIRED for tool-calling agents; procedural agents omit it).
- `{extension_root}/configs/agents/{name}.py` — the config dict.

**Registration is automatic via a hook**: after writing the files, include the Python file path in your `done_tool` reasoning — the `registration_hook` locates and registers it. The class name in `done_tool` reasoning helps it resolve.

---

## Choosing the agent type

- **Tool-calling agent** (default): reasons and acts step by step, choosing tools/skills dynamically each step. Use it for open-ended or multi-step tasks. It has a Python class + an HTML prompt, and it **inherits** the base loop. → `tool_calling_agent_template.py` + `html_prompt_template.html`.
- **Procedural agent**: a fixed, deterministic pipeline (read → process → report) expressed in code with direct tool calls — no step-by-step LLM planning, no prompt. It overrides `__call__` and calls no model; the kernel does not care what is inside. → `procedural_agent_template.py`.

When unsure, prefer a tool-calling agent — it's the more general, more capable form.

---

## Creating an agent

### The class is THIN — inherit, don't reinvent

The base `Agent` already implements the standard think-and-act loop (`__call__`) and the context builder (`_get_agent_context`, `_get_messages`, `_think_and_act`). A well-formed tool-calling agent **inherits all of it** and only supplies:
- its identity fields (`name`, `description`, `metadata`, `enable_evolving`),
- an `__init__` that sets `prompt_name` (must match the HTML prompt's `<meta name="name">`),
- a thin `__call__` that delegates to `super().__call__(...)`.

**Do NOT override** `_get_agent_context`, `_get_messages`, or `_think_and_act` unless the agent genuinely needs bespoke behavior — reviewers treat unnecessary overrides as a defect. The only common reason to put real logic in `__call__` is an agent that must **register a produced artifact**: it calls `super().__call__(...)`, then fires a registration hook on the result (see the variant in `tool_calling_agent_template.py`).

Steps:
1. Read `tool_calling_agent_template.py`, copy it to `{extension_root}/agent/{name}.py`, rename the class, and fill `name` / `description` (state what it does AND when to use it) / `prompt_name`.
2. Write the HTML prompt (next section).
3. `python -m py_compile /abs/path/{name}.py`; then put the `.py` path in `done_tool` reasoning to register.

### Writing the HTML prompt (this is where agent quality lives)

Copy `html_prompt_template.html` to `{extension_root}/prompt/{name}.html`, set `<meta name="name">` to the agent's name, and fill each block. The prompt is the agent's brain — treat it with the same care as a skill.

**Structure (do not break it):**
- **system**: `profile`, `language-settings`, `project`, `input-rules`, `constraint-rules`, `task-rules`, `context-rules`, `response-protocol`.
- **user**: stable-to-live ordering — `<capability-context>` (tools, skills, connectors,
  plugins, workflows, sub-agents), optional `<environment-context>`, then `<agent-context>`
  (task, inherited context, plan, constraints, step info, environment state, workspace,
  errors). The CSS, renderer, context builder, and prompt cache depend on this layout.
  - **The order is not cosmetic.** Stable catalogs precede live state so a changing step does
    not invalidate the reusable prefix. The ContextBuilder then sends callable catalogs as
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
