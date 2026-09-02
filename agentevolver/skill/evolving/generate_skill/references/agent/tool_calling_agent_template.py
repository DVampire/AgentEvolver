"""TEMPLATE — a tool-calling agent (the LLM think-and-act loop).

Copy to `{extension_root}/agent/{name}.py`, rename the class, fill in name/description,
and pair it with an HTML prompt at `{extension_root}/prompt/{name}.html` (see
`html_prompt_template.html`). This is the common agent type: it reasons and acts step by
step over tools, skills and connectors, driven entirely by the base-class loop.

KEY RULE — the class is a DECLARATION, not an implementation.

`Agent` already owns the loop (`__call__` → `think` → `act`), prompt assembly
(`agent/context/`), the executor and the router. Every real actor in
`agentevolver/agent/actor/` is field declarations and nothing else, and that is the
target shape: a name, a prompt, a step budget. Overriding a loop method is either a
genuine new behaviour or an accident, and reviewers treat it as the latter by default.

Do NOT write an `__init__`. The base takes `base_dir` and forwards keyword arguments to
pydantic, so declaring fields is the whole of configuration. An earlier version of this
template hand-wrote one that defaulted every field to `None` and passed those through;
pydantic rejects `None` for a `str`, so every agent generated from it failed to
construct. If a field genuinely needs computing, override `__init__(self, base_dir: str
= "", **kwargs)` and call `super().__init__(base_dir=base_dir, **kwargs)` first.

The seams that exist for a reason, when a declaration truly is not enough:

    prompt_modules()      extra prompt sections
    project_context()     what the agent is told about the workspace
    working_memory()      what it carries between steps
    on_step()             advice for this step — prefer middleware in `loop/guards.py`
    completion_blocker()  a reason this run may not finish yet
    finalize()            shape the final Response
    on_start/on_land/on_exit/on_suspend/on_resume    runtime phases
"""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class MyAgent(Agent):
    """One-line purpose — what this agent does and when it is dispatched."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="my_agent")
    #: How it gets chosen. The router projects this to the model as a tool schema, so it
    #: has to say when to use the agent, not only what it is.
    description: str = Field(
        default="What this agent does AND when to use it."
    )
    metadata: Dict[str, Any] = Field(default={})
    #: Must match the HTML prompt's `<meta name="name">`.
    prompt_name: str = Field(default="my_agent")
    max_step: int = Field(default=20)
    #: True lets the optimize agent edit this file.
    enable_evolving: bool = Field(default=True)


__all__ = ["MyAgent"]
