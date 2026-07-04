"""SkillOptimizeAgent — evolves an existing skill's SKILL.md given an optimization task."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext
from src.response.types import Response
from src.registry import AGENT


@AGENT.register_module(force=True)
class SkillOptimizeAgent(Agent):
    """Evolves an existing skill (its SKILL.md and resources) to satisfy an optimization task.

    Runs the base-class standard loop, then re-registers the edited skill inline in
    ``__call__``. The target skill is named in the task; the agent should call
    ``inspect_skill`` first to confirm it is registered and evolvable (require_grad=True)
    and to obtain its directory — a frozen skill must NOT be optimized."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="skill_optimize_agent")
    description: str = Field(
        default="An agent that evolves a skill's SKILL.md given an optimization task."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=False)

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
        require_grad: bool = False,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "skill_optimize_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    async def __call__(
        self,
        task: Optional[str] = None,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Response:
        """Run the base loop, then reload and re-register the edited skill."""
        from src.hook.server import hook_manager
        from src.hook.types import HookDecision, HookEvent
        from src.utils import get_project_root

        if ctx is None:
            ctx = AgentContext()
        response = await super().__call__(task=task, files=files, ctx=ctx, **kwargs)

        if response.success:
            result = await hook_manager(
                name="skill_registration_hook",
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
                response.message = result.reason or "Re-registration failed; include the edited SKILL.md path in the done_tool reasoning."
        return response
