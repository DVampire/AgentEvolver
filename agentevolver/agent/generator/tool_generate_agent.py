"""ToolGenerateAgent — generates new tool source code from a description."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.response.types import Response
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class ToolGenerateAgent(Agent):
    """Generates a new tool (Python source) from a natural-language description.

    Runs the base-class standard loop, then registers the generated tool inline in
    ``__call__``. The tool name comes from the task text; the agent writes the source
    under the conventional ``extension/`` path and reports it in its done_tool reasoning
    so the registration hook can locate it."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="tool_generate_agent")
    description: str = Field(
        default="An agent that generates new tool source code from a description."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False)

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
        max_step: int = 30,
        review_steps: int = 5,
        enable_evolving: bool = False,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "tool_generate_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            enable_evolving=enable_evolving,
            **kwargs,
        )

    async def _finalize_run(self, response, ctx):
        """Run the base loop, then register the freshly generated tool."""
        from agentevolver.hook.server import hook_manager
        from agentevolver.hook.types import HookDecision, HookEvent
        from agentevolver.utils import get_project_root

        if response.success:
            result = await hook_manager(
                name="tool_registration_hook",
                input={
                    "event": HookEvent.ON_STOP,
                    "reasoning": (response.data or {}).get("reasoning") or "",
                    "project_root": get_project_root(),
                    "model_name": self.model_name,
                },
                ctx=ctx,
            )
            if result.decision == HookDecision.BLOCK:
                response.success = False
                response.message = result.reason or "Registration failed; include the generated tool file path in the done_tool reasoning."
        return response
