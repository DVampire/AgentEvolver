---
name: agent_creator_skill
description: Create new agents, improve/optimize existing agents (both Python class and HTML prompt), and evaluate agent quality — the full agent lifecycle in this framework. Use whenever the task involves authoring a new agent, editing/improving an existing agent or its prompt, or evaluating/scoring an agent. MetaAgent uses it to orchestrate the create→evaluate→improve loop across sub-agents.
version: 1.1.0
type: [orchestrator, worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Agent Creator

A single skill for the full lifecycle of **agents**: creating new ones, improving existing ones (class *and* prompt), and evaluating their quality. An agent in this framework is a thin Python class over the shared base `Agent`, usually paired with an HTML prompt (for tool-calling agents) and a config.

At a high level, building a good agent is a loop: decide what it should do → pick the agent type → write a thin class + a focused prompt from the templates → run it on realistic tasks → evaluate → improve the prompt/class → repeat.

## How this skill is used — four roles, one body of knowledge

- **MetaAgent (orchestrator role)** — drives the create→evaluate→improve loop, dispatching the sub-agents. See **Orchestration**.
- **agent_generate_agent** — reads **Creating an agent**.
- **agent_optimize_agent** — reads **Improving an agent** (this is where most of the value is — prompt tuning).
- **agent_evaluate_agent** — reads **Evaluating an agent**.

The sub-agents are headless: each runs one phase autonomously and returns a result. There is no human-in-the-loop review.

## Reference templates — read before writing

Start from the bundled templates instead of writing from scratch:
- `references/tool_calling_agent_template.py` — a thin tool-calling agent class (the common case).
- `references/workflow_agent_template.py` — a deterministic, code-driven agent (no LLM loop, no prompt).
- `references/html_prompt_template.html` — the HTML prompt skeleton for a tool-calling agent.

Read the relevant template(s) first, copy, then adapt. They already encode the current architecture and the template-variable contract.

## Framework conventions (read once)

An agent has up to three files:
- `{project_root}/extension/agent/{name}.py` — the Python class (REQUIRED).
- `{project_root}/extension/prompt/{name}.html` — the HTML prompt (REQUIRED for tool-calling agents; workflow agents omit it).
- `{project_root}/configs/agents/{name}.py` — the config dict.

**Registration is automatic via a hook**: after writing the files, include the Python file path in your `done_tool` reasoning — the `agent_registration_hook` locates and registers it. The class name in `done_tool` reasoning helps it resolve.

---

## Choosing the agent type

- **Tool-calling agent** (default): reasons and acts step by step, choosing tools/skills dynamically each step. Use it for open-ended or multi-step tasks. It has a Python class + an HTML prompt, and it **inherits** the base loop. → `tool_calling_agent_template.py` + `html_prompt_template.html`.
- **Workflow agent**: a fixed, deterministic pipeline (read → process → report) expressed in code with direct tool calls — no step-by-step LLM planning, no prompt. Use it when the steps are known and don't need reasoning. → `workflow_agent_template.py`.

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
1. Read `tool_calling_agent_template.py`, copy it to `extension/agent/{name}.py`, rename the class, and fill `name` / `description` (state what it does AND when to use it) / `prompt_name`.
2. Write the HTML prompt (next section).
3. `python -m py_compile /abs/path/{name}.py`; then put the `.py` path in `done_tool` reasoning to register.

### Writing the HTML prompt (this is where agent quality lives)

Copy `html_prompt_template.html` to `extension/prompt/{name}.html`, set `<meta name="name">` to the agent's name, and fill each block. The prompt is the agent's brain — treat it with the same care as a skill.

**Structure (do not break it):**
- **system**: `profile`, `language-settings`, `project`, `input-rules`, `constraint-rules`, `task-rules`, `context-rules`, `plan-rules`, `output-format`, `output-schema`.
- **user**: an `<agent-context>` **container** holding `task` / `constraints` / `step-info` / `memory` / (`todo`) / `workspace` / (`errors`), with `<tool-context>`, `<skill-context>`, `<connector-context>` as **siblings** of `<agent-context>` (NOT nested). The CSS/renderer depends on this container-vs-sibling layout.

**Template-variable contract** — use only the variables the base context builder provides, spelled exactly:
`{{ task }}`, `{{ constraint_text }}`, `{{ step_info }}`, `{{ memory_context }}`, `{{ workspace }}`, `{{ errors }}`, `{{ todo }}`, `{{ available_tools }}`, `{{ available_skills }}`, `{{ available_connectors }}`, plus the system-side `{{ project_root }}`, `{{ work_dir }}`, `{{ max_actions }}`. Inventing a variable leaves an empty slot; misspelling one silently drops that context.

**The `output-schema` must match what the loop parses**: an object with a `reasoning` string and a `plan` array of `{description, action:{type,name,args}}`, where `args` is a JSON *string*. Keep this exact shape — the loop deserializes it every step.

**Writing principles (borrowed from good skill authoring):**
- Prefer the imperative. Define concrete rules, not vague directives.
- **Explain the WHY.** Modern models have good theory of mind — when you explain why a rule matters, they generalize instead of following it brittly. All-caps ALWAYS/NEVER and rigid structures are a yellow flag: reframe as reasoning.
- Keep it lean — every line should earn its place. Remove instructions that don't change behavior.
- Make the `profile` and `task-rules` say clearly what the agent is for and how it should approach work.
- Draft it, then reread with fresh eyes and cut/clarify.

---

## Evaluating an agent

Call `inspect_agent_tool` on the target for its registry facts (registered / instantiated / version / file paths). Score across five dimensions (0–20 each):

1. **Interface Compliance** — `@AGENT.register_module`, inherits `Agent`, has `name`/`description`/`metadata`/`enable_evolving`; **cleanly inherits the base loop** (does NOT re-implement `__call__`/`_get_agent_context`/`_think_and_act` without reason — a generator overriding only `__call__` to register is fine; a workflow agent legitimately overrides `__call__`).
2. **Code Quality** — clean, valid, no dead code; lifecycle hooks come from the inherited loop, not re-implemented.
3. **Prompt Quality** — HTML present (tool-calling) with the required sections, the container-vs-sibling `agent-context` layout, correct template variables, and an `output-schema` matching the loop. Auto-pass for workflow agents (no prompt).
4. **Integration** — `inspect_agent_tool` shows Registered + Instantiated.
5. **Task Execution** — a valid execution path: a tool-calling agent with a valid `prompt_name` inheriting the loop, or a coherent bespoke `__call__` for a workflow agent.

For an empirical check, MetaAgent can dispatch the agent on a sample task and inspect the result.

---

## Improving an agent

Most agent improvement is **prompt improvement**. The target is named in the task. Call `inspect_agent_tool` FIRST for its file paths and `enable_evolving` — if `enable_evolving=False`, the agent is frozen; do NOT edit it, report and stop.

- Read the Python and HTML before editing. Decide whether the fix is in the **prompt** (behavior, rules, reasoning — most common) or the **class** (a real code bug).
- Make the smallest correct change. Preserve `@AGENT.register_module`, `name`, and the prompt's `agent-context` structure / template variables / output-schema.
- Keep the class thin — prefer fixing the prompt over adding loop overrides. If you see an unnecessary override of `_get_agent_context`/`_think_and_act`, that's a candidate to remove.
- Apply the prompt writing principles above: explain the why, cut dead instructions, sharpen the rules the agent kept getting wrong.
- Verify with `py_compile` (and that the HTML still has valid template variables), then re-register by putting the edited file path in `done_tool` reasoning.

---

## Orchestration (for MetaAgent)

1. **Generate** — dispatch `agent_generate_agent` with the intent; it writes the class/prompt/config from the templates and registers.
2. **Evaluate** — dispatch `agent_evaluate_agent` (optionally after a sample run) to score.
3. **Improve** — dispatch `agent_optimize_agent` with the evaluation; it edits (usually the prompt) and re-registers.
4. **Repeat** until the agent is good.

---

## Reference files

- `references/tool_calling_agent_template.py` — thin tool-calling agent class.
- `references/workflow_agent_template.py` — deterministic workflow agent.
- `references/html_prompt_template.html` — HTML prompt skeleton (structure + template-variable contract).
