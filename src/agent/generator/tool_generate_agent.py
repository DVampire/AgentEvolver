"""ToolGenerateAgent — an agent that generates new tool source code from a description."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext
from src.response.types import Response, ResponseType
from src.hook.server import hook_manager
from src.logger import logger
from src.registry import AGENT
from src.tool.server import tool_manager



@AGENT.register_module(force=True)
class ToolGenerateAgent(Agent):
    """Agent that generates a new tool from a natural-language description.

    Receives a generation task from MetaAgent describing what the new tool should do,
    writes a new .py file under src/tool/extended/, verifies it, registers it, and
    reports back via done_tool.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="tool_generate_agent")
    description: str = Field(
        default="An agent that generates new tool source code from a description."
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
        max_steps: int = 30,
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
            prompt_name=prompt_name or "tool_generate_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Override: inject generation context
    # ------------------------------------------------------------------

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        target_name = kwargs.get("target_name")
        lines = []
        if target_name:
            lines.append(f"- **Requested Tool Name**: `{target_name}`")
            lines.append(f"- **Target File**: `src/tool/extended/{target_name}.py`")
            existing = await tool_manager.get_info(target_name)
            if existing:
                lines.append(f"- **Status**: already registered (version {existing.version}) — regenerate/overwrite if instructed")
            else:
                lines.append("- **Status**: not yet registered — create from scratch")
        else:
            lines.append("- **Requested Tool Name**: (not specified — infer a snake_case name from the task)")
            lines.append("- **Target File**: `src/tool/extended/<inferred_name>.py`")

        base["generation_target"] = "\n".join(lines)

        action_errors = kwargs.get("action_errors") or []
        if action_errors:
            error_lines = "\n".join(f"- {e}" for e in action_errors)
            base["agent_context"] += f"\n\n### Previous Step Errors\n{error_lines}"

        return base

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        target_name: Optional[str] = None,
        **kwargs,
    ) -> Response:
        from src.utils.name_utils import make_id
        from src.hook.types import HookDecision, HookEvent
        from src.utils import get_project_root

        logger.info(f"| 🚀 Starting {self.name}: {task} (target_name={target_name})")
        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir
        task_id = make_id()

        await hook_manager(
            name="memory_hook",
            input={
                "event": HookEvent.ON_START,
                "agent_name": self.name,
                "task_id": task_id,
                "task": task,
                "target_name": target_name,
                "memory_name": self.memory_name,
                "use_memory": self.use_memory,
            },
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={
                "event": HookEvent.ON_START,
                "agent_name": self.name,
                "task_id": task_id,
                "task": task,
                "target_name": target_name,
                "memory_name": self.memory_name,
                "use_memory": self.use_memory,
            },
            ctx=ctx,
        )

        messages = await self._get_messages(task, ctx=ctx, target_name=target_name)
        step_number = 0
        response = {"done": False, "result": None, "reasoning": None, "action_errors": []}

        while step_number < self.max_steps:
            logger.info(f"| 🔄 [{self.name}] Step {step_number + 1}/{self.max_steps}")
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx, target_name=target_name
            )
            step_number += 1
            action_errors = response.get("action_errors") or []

            if response["done"]:
                hook_result = await hook_manager(
                    name="tool_registration_hook",
                    input={
                        "target_name": target_name,
                        "reasoning": response.get("reasoning") or "",
                        "project_root": get_project_root(),
                        "model_name": self.model_name,
                    },
                    ctx=ctx,
                )
                if hook_result.decision == HookDecision.BLOCK:
                    response["done"] = False
                    action_errors = [hook_result.reason or "Registration failed."]
                else:
                    break

            messages = await self._get_messages(
                task, ctx=ctx, target_name=target_name, action_errors=action_errors
            )

        if step_number >= self.max_steps and not response["done"]:
            logger.warning(f"| 🛑 [{self.name}] Reached max steps ({self.max_steps})")
            response["result"] = f"{self.name} did not complete within max steps."

        await hook_manager(
            name="memory_hook",
            input={
                "event": HookEvent.ON_STOP,
                "agent_name": self.name,
                "task_id": task_id,
                "result": response.get("result"),
                "memory_name": self.memory_name,
                "use_memory": self.use_memory,
            },
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={
                "event": HookEvent.ON_STOP,
                "agent_name": self.name,
                "task_id": task_id,
                "result": response.get("result"),
                "memory_name": self.memory_name,
                "use_memory": self.use_memory,
            },
            ctx=ctx,
        )

        return Response(type=ResponseType.AGENT, 
            success=response["done"],
            message=response["result"] or "",
            data=response,
        )
