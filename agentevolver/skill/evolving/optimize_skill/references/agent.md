# Improving an agent

## What it is

An agent is a Python file under `{extension_root}/agent/`, usually paired with an HTML
prompt at `{extension_root}/prompt/{name}.html`. Changing only the prompt is a complete, valid
change.

**The contract**: the class stays thin — it inherits the standard loop rather than
reimplementing it — and the prompt puts `<capability-context>` before `<agent-context>` in the
user turn, because nothing after the state can be cached.

## Improving an agent

Most agent improvement is **prompt improvement**. The target is named in the task. Call `inspect_tool` (`capability_type="agent"`) FIRST for its file paths and `enable_evolving` — if `enable_evolving=False`, the agent is frozen; do NOT edit it, report and stop. (The built-in default agents are all frozen; the optimizer edits generated agents under `extension/`, which keep every block inline.)

- Read the Python and HTML before editing. Decide whether the fix is in the **prompt** (behavior, rules, reasoning — most common) or the **class** (a real code bug).
- Make the smallest correct change. Preserve `@AGENT.register_module`, `name`, and the prompt's `agent-context` structure and template variables.
- If you ever edit a `agentevolver/prompt/default/` agent and see a `<module src="../module/NAME.html">` tag, that block is a **shared module** used by many agents — editing the module file changes all of them; to change one agent only, inline the block into that file first.
- Keep the class a declaration — prefer fixing the prompt over adding loop overrides. An actor that overrides `think`, `act`, or anything in `agent/context/` is a candidate to remove: the loop, the assembler and the executor are the same for every agent, so an override is either a genuine new behaviour or an accident. Advice for the model belongs in a step middleware (`agent/loop/guards.py`), not in an override.
- Apply the prompt writing principles above: explain the why, cut dead instructions, sharpen the rules the agent kept getting wrong.
- Verify with `py_compile` (and that the HTML still has valid template variables), then re-register by putting the edited file path in `done_tool` reasoning.

---
