"""ToolOptimizeAgent — an agent that evolves tool source code given an evolution task."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext
from src.response.types import Response, ResponseType
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.registry import AGENT
from src.tool.server import tool_manager
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class ToolOptimizeAgent(Agent):
    """Agent that evolves tool source code to satisfy an evolution task.

    Receives an evolution task from MetaAgent, iteratively reads / edits the target
    tool's .py file under src/tool/extended/ using standard file/bash tools, verifies
    the change, and reports back via done_tool — identical execution loop to CodeAgent.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="tool_optimize_agent")
    description: str = Field(
        default="An agent that evolves tool source code given an evolution task."
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
            prompt_name=prompt_name or "tool_optimize_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Override: inject tool source file snapshot into agent context
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
        if target_name:
            tool_config = await tool_manager.get_info(target_name)
            lines = [f"- **Tool Name**: {target_name}"]
            if tool_config:
                lines.append(f"- **Description**: {tool_config.description}")
                lines.append(f"- **Version**: {tool_config.version}")
                if tool_config.path:
                    lines.append(f"- **Source File**: {tool_config.path}")
            else:
                lines.append("- (tool not found in registry)")
            base["optimization_target"] = "\n".join(lines)
        else:
            base["optimization_target"] = "(no target_name provided)"

        base["workspace"] = self._workspace_snapshot(ctx)

        action_errors = kwargs.get("action_errors") or []
        base["errors"] = "\n".join(f"- {e}" for e in action_errors) if action_errors else ""

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
        logger.info(f"| 🚀 Starting {self.name}: {task} (tool={target_name})")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        if not target_name:
            logger.warning(f"| ⚠️ {self.name} called without target_name")
            return Response(type=ResponseType.AGENT, success=False, message="target_name is required for optimization but was not provided.")

        tool_config = await tool_manager.get_info(target_name)
        if tool_config is None:
            logger.warning(f"| ⚠️ Tool '{target_name}' not found in registry, refusing optimization")
            return Response(type=ResponseType.AGENT, success=False, message=f"Tool '{target_name}' not found in registry.")
        if not tool_config.require_grad:
            logger.warning(f"| ⚠️ Tool '{target_name}' has require_grad=False, refusing optimization")
            return Response(type=ResponseType.AGENT, success=False, message=f"Tool '{target_name}' is not evolvable (require_grad=False).")

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

        step_number = 0
        action_errors = []
        response = {"done": False, "result": None, "reasoning": None, "action_errors": []}

        while step_number < self.max_step:
            logger.info(f"| 🔄 [{self.name}] Step {step_number + 1}/{self.max_step}")
            reason, constraint_status = await self._constraint_check(task_id, ctx)
            if reason is not None:
                logger.warning(f"| 🛑 {self.name} constraint violated: {reason}")
                response = {"done": True, "result": reason, "reasoning": None,
                            "action_errors": [], "stopped_by_constraint": True}
                break
            messages = await self._get_messages(
                task, ctx=ctx, target_name=target_name,
                step_number=step_number, action_errors=action_errors,
                constraint_status=constraint_status,
            )
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx, target_name=target_name
            )
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response["done"]:
                break

        if step_number >= self.max_step and not response["done"]:
            logger.warning(f"| 🛑 [{self.name}] Reached max steps ({self.max_step})")
            response["result"] = f"{self.name} did not complete within max steps."

        if response["done"]:
            from src.utils import get_project_root
            await hook_manager(
                name="tool_registration_hook",
                input={
                    "target_name": target_name,
                    "reasoning": response.get("reasoning") or "",
                    "project_root": get_project_root(),
                },
                ctx=ctx,
            )

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
            success=response["done"] and not response.get("stopped_by_constraint", False),
            message=response["result"] or "",
            data=response,
        )

