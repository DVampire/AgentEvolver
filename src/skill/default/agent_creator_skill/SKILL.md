---
name: agent_creator_skill
description: Create new agents, improve/optimize existing agents, and evaluate agent quality — the full agent lifecycle in this framework. Use whenever the task involves authoring a new agent (Python class + HTML prompt + config), editing/improving an existing agent, or evaluating/scoring an agent. MetaAgent uses it to orchestrate the create→evaluate→improve loop across sub-agents.
version: 1.0.0
type: [orchestrator, worker]
category: meta
requirements: [cpu]
metadata: {}
---

# Agent Creator

A single skill for the full lifecycle of **agents**: creating new ones, improving existing ones, and evaluating their quality. An agent in this framework is a thin Python class over the shared base `Agent`, paired with an HTML prompt (for tool-calling agents) and a config.

## How this skill is used — four roles, one body of knowledge

- **MetaAgent (orchestrator role)** — drives the create→evaluate→improve loop, dispatching the sub-agents. See **Orchestration**.
- **agent_generate_agent** — reads **Creating an agent**.
- **agent_optimize_agent** — reads **Improving an agent**.
- **agent_evaluate_agent** — reads **Evaluating an agent**.

The sub-agents are headless: each runs one phase autonomously and returns a result. There is no human-in-the-loop review.

## Framework conventions (read once)

An agent has up to three files:
- `{project_root}/extension/agent/{name}.py` — the Python class (REQUIRED).
- `{project_root}/extension/prompt/{name}.html` — the HTML prompt (REQUIRED for tool-calling agents; workflow agents may omit it).
- `{project_root}/configs/agents/{name}.py` — the config dict.

**Registration is automatic via a hook**: after writing the files, include the Python file path in your `done_tool` reasoning — the `agent_registration_hook` locates and registers it.

### The class is THIN — inherit, don't reinvent

The base `Agent` already implements the standard think-and-act loop (`__call__`) and the context builder (`_get_agent_context`, `_get_messages`, `_think_and_act`). A well-formed agent **inherits** all of it and only supplies its identity + prompt:

```python
from typing import Any, Dict, List, Optional
from pydantic import ConfigDict, Field
from src.registry import AGENT
from src.agent.types import Agent, AgentContext
from src.response.types import Response


@AGENT.register_module(force=True)
class MyAgent(Agent):
    """One-line purpose."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="my_agent")
    description: str = Field(default="What this agent does and when to use it.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=False)

    def __init__(self, base_dir: str, name=None, description=None, metadata=None,
                 model_name=None, prompt_name=None, memory_name=None,
                 max_actions: int = 10, max_step: int = 20, review_steps: int = 5,
                 require_grad: bool = False, **kwargs):
        super().__init__(base_dir=base_dir, name=name, description=description,
                         metadata=metadata, model_name=model_name,
                         prompt_name=prompt_name or "my_agent", memory_name=memory_name,
                         max_actions=max_actions, max_step=max_step,
                         review_steps=review_steps, require_grad=require_grad, **kwargs)

    async def __call__(self, task: Optional[str] = None, files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None, **kwargs) -> Response:
        """Entry point — runs the base-class standard loop unchanged."""
        return await super().__call__(task=task, files=files, ctx=ctx, **kwargs)
```

- **Do NOT override** `_get_agent_context`, `_get_messages`, or `_think_and_act` unless the agent genuinely needs bespoke behavior — overriding them is a red flag.
- The `__call__` is a thin entry point that delegates to `super().__call__`. Only override it with real logic if the agent must finalize a produced artifact (e.g. a generator that registers what it created — it runs `super().__call__(...)` then fires a registration hook on the result).
- Event-driven orchestrators (like MetaAgent) override `on_start`/`on_event` instead; an agent with a genuinely different loop overrides `__call__` fully.

### The HTML prompt

For a tool-calling agent, mirror the structure of `general_agent.html`:
- **system**: `profile`, `language-settings`, `input-rules`, `constraint-rules`, `task-rules`, `context-rules`, `plan-rules`, `output-schema`.
- **user**: an `agent-context` container holding `task` / `constraints` / `step-info` / `memory` / (`todo`) / `workspace` / (`errors`), with `tool-context`, `skill-context`, `connector-context` as **siblings** of `agent-context`.
- Use the template variables the base context builder provides: `{{ task }}`, `{{ constraint_text }}`, `{{ step_info }}`, `{{ memory_context }}`, `{{ workspace }}`, `{{ errors }}`, `{{ todo }}`, `{{ available_tools }}`, `{{ available_skills }}`, `{{ available_connectors }}`.

### Verify and register

After writing the Python file: `python -m py_compile /abs/path/{name}.py`. When it compiles, put the `.py` path in your `done_tool` reasoning so the registration hook installs it.

---

## Evaluating an agent

Call `inspect_agent` on the target to get its registry facts (registered / instantiated / version / file paths). Score across:
1. **Interface Compliance** — `@AGENT.register_module`, inherits `Agent`, has `name`/`description`/`metadata`/`require_grad`; **cleanly inherits the base loop** (does NOT re-implement `__call__`/`_get_agent_context`/`_think_and_act` without reason); a generator overrides only `__call__` to register.
2. **Code Quality** — clean, valid, no dead code; lifecycle hooks come from the inherited loop, not re-implemented.
3. **Prompt Quality** — HTML present (tool-calling) with the required sections and correct template variables (auto-pass for workflow agents).
4. **Integration** — `inspect_agent` shows Registered + Instantiated.
5. **Task Execution** — a valid execution path (inherited loop with a valid `prompt_name`, or a coherent bespoke `__call__`).

For an empirical check, MetaAgent can dispatch the agent on a sample task and inspect the result.

---

## Improving an agent

The target is named in the task. Call `inspect_agent` FIRST for its file paths and `require_grad` — if `require_grad=False`, the agent is frozen; do NOT edit it, report and stop. Read the Python (and HTML) before editing; make the smallest correct change; preserve the `@AGENT.register_module` decorator and `name`; keep the class thin (prefer fixing the prompt over adding loop overrides). Verify with `py_compile`, then re-register by putting the edited file path in `done_tool` reasoning.

---

## Orchestration (for MetaAgent)

1. **Generate** — dispatch `agent_generate_agent` with the intent; it writes the class/prompt/config and registers.
2. **Evaluate** — dispatch `agent_evaluate_agent` (optionally after a sample run) to score.
3. **Improve** — dispatch `agent_optimize_agent` with the evaluation; it edits and re-registers.
4. **Repeat** until the agent is good.
