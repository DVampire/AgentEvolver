"""TEMPLATE — a tool-calling agent (LLM think-and-act loop).

Copy to `extension/agent/{name}.py`, rename the class, fill name/description, and
pair it with an HTML prompt at `extension/prompt/{name}.html` (see
`html_prompt_template.html`). This is the common agent type: it reasons and acts
step by step using tools/skills/connectors, driven by the base-class loop.

KEY RULE — the class is THIN. The base `Agent` already implements the full
standard loop (`__call__`) and context builder (`_get_agent_context`,
`_get_messages`, `_think_and_act`). Inherit all of it. Do NOT re-implement the
loop or override the context methods unless the agent truly needs bespoke behavior
(that is a red flag reviewers look for). Supply identity + prompt; inherit the rest.
"""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.registry import AGENT
from agentevolver.agent.types import Agent, AgentContext
from agentevolver.response.types import Response


@AGENT.register_module(force=True)
class MyAgent(Agent):
    """One-line purpose — what this agent does and when it is dispatched."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="my_agent")
    description: str = Field(
        default="What this agent does AND when to use it (the description is how it gets chosen)."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # enable_evolving=True marks the agent as evolvable (the optimize agent may edit it).
    enable_evolving: bool = Field(default=True)

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        max_actions: int = 10,
        max_step: int = 20,
        review_steps: int = 5,
        enable_evolving: bool = True,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "my_agent",  # must match the HTML prompt's <meta name="name">
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            enable_evolving=enable_evolving,
            **kwargs,
        )

    async def __call__(
        self,
        task: Optional[str] = None,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Response:
        """Entry point — runs the base-class standard think-and-act loop unchanged.

        The base loop already handles: ON_START hooks → [constraint check → build
        messages → think & act] until done/max_step → ON_STOP hooks. Just delegate.

        ── Variant: register a produced artifact ──────────────────────────────
        If this agent PRODUCES something that must be registered (like the
        generator agents), run the loop and then fire the registration hook inline:

            from agentevolver.hook.server import hook_manager
            from agentevolver.hook.types import HookDecision, HookEvent
            from agentevolver.utils import get_project_root
            if ctx is None:
                ctx = AgentContext()
            response = await super().__call__(task=task, files=files, ctx=ctx, **kwargs)
            if response.success:
                result = await hook_manager(name="<x>_registration_hook", input={
                    "event": HookEvent.ON_STOP,
                    "reasoning": (response.data or {}).get("reasoning") or "",
                    "project_root": get_project_root(), "model_name": self.model_name}, ctx=ctx)
                if result.decision == HookDecision.BLOCK:
                    response.success = False
                    response.message = result.reason or "Registration failed."
            return response
        """
        return await super().__call__(task=task, files=files, ctx=ctx, **kwargs)
