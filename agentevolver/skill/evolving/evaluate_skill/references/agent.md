# Evaluating an agent

## What it is

An agent is a Python file under `{extension_root}/agent/`, usually paired with an HTML
prompt at `{extension_root}/prompt/{name}.html`. Changing only the prompt is a complete, valid
change.

**The contract**: the class stays thin — it inherits the standard loop rather than
reimplementing it — and the prompt puts `<capability-context>` before `<agent-context>` in the
user turn, because nothing after the state can be cached.

## Evaluating an agent

Call `inspect_tool` (`capability_type="agent"`) on the target for its registry facts (registered / instantiated / version / file paths). Score across five dimensions (0–20 each):

1. **Interface Compliance** — `@AGENT.register_module`, inherits `Agent`, has `name`/`description`/`metadata`/`enable_evolving`; **cleanly inherits the base loop**. Tool-calling agents must not override `__call__`; an agent whose main function is code overrides `__call__` and calls no model.
2. **Code Quality** — clean, valid, no dead code; lifecycle hooks come from the inherited loop, not re-implemented.
3. **Prompt Quality** — HTML present (tool-calling) with the required sections, the container-vs-sibling `agent-context` layout, correct template variables, and a `response-protocol` that drives **native tool calls** (no obsolete JSON `plan`/`output-schema`). Auto-pass for procedural agents (no prompt).
4. **Integration** — `inspect_tool` (`capability_type="agent"`) shows Registered + Instantiated.
5. **Task Execution** — a valid execution path: a tool-calling agent with a valid `prompt_name` inheriting the loop, or a code-driven agent overriding `__call__`.

For an empirical check, MetaAgent can dispatch the agent on a sample task and inspect the result.

---
