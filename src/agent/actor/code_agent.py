"""CodeAgent — a code-focused agent that reads, edits, and commits code."""

from typing import List, Optional, Dict, Any
from pydantic import Field, ConfigDict

from src.logger import logger
from src.registry import AGENT
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.agent.types import (
    Agent,
        AgentContext,
)
from src.response.types import Response, ResponseType
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class CodeAgent(Agent):
    """Code agent that reads, edits, and commits code using file and git tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="code_agent")
    description: str = Field(
        default="A code agent that reads, writes, and edits source code files, "
        "runs tests, and commits changes using git."
    )
    metadata: Dict[str, Any] = Field(default={})
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
            prompt_name=prompt_name or "code_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        base = await super()._get_agent_context(
            task, step_number=step_number, ctx=ctx, **kwargs
        )

        # Live workspace listing (see workspace sub-module in code_agent.html).
        base["workspace"] = self._workspace_snapshot(ctx)

        action_errors = kwargs.get("action_errors") or []
        base["errors"] = (
            "\n".join(f"- {e}" for e in action_errors) if action_errors else ""
        )

        return base

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> Response:
        logger.info(f"| 🚀 Starting CodeAgent: {task}")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        # Inject workdir so git_tool and file tools can resolve paths
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        if files:
            logger.info(f"| 📂 Attached files: {files}")
        enhanced_task = task

        task_id = make_id()
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        # ON_START
        await hook_manager(
            name="memory_hook",
            input={
                "event": HookEvent.ON_START,
                "agent_name": self.name,
                "task_id": task_id,
                "task": enhanced_task,
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
                "task": enhanced_task,
                "memory_name": self.memory_name,
                "use_memory": self.use_memory,
            },
            ctx=ctx,
        )

        step_number = 0
        action_errors: List[str] = []
        response = {
            "done": False,
            "result": None,
            "reasoning": None,
            "action_errors": [],
        }

        while step_number < self.max_step:
            logger.info(f"| 🔄 Step {step_number+1}/{self.max_step}")
            # Check budget BEFORE building the message, so the prompt reflects the
            # current budget (aligned with MetaAgent). The check runs exactly once
            # per step; _think_and_act is told not to repeat it.
            reason, constraint_status = await self._constraint_check(task_id, ctx)
            if reason is not None:
                logger.warning(f"| 🛑 {self.name} constraint violated: {reason}")
                response = {"done": True, "result": reason, "reasoning": None,
                            "action_errors": [], "stopped_by_constraint": True}
                break
            messages = await self._get_messages(
                enhanced_task,
                ctx=ctx,
                files=files,
                step_number=step_number,
                action_errors=action_errors,
                constraint_status=constraint_status,
            )
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx
            )
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response["done"]:
                break

        if step_number >= self.max_step and not response["done"]:
            logger.warning(f"| 🛑 Reached max steps ({self.max_step})")
            response = {
                "done": False,
                "result": "The task has not been completed.",
                "reasoning": "Reached the maximum number of steps.",
            }

        # ON_STOP
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

        logger.info(
            f"| ✅ CodeAgent completed after {step_number}/{self.max_step} steps"
        )

        return Response(type=ResponseType.AGENT, 
            success=response["done"] and not response.get("stopped_by_constraint", False),
            message=response["result"],
            data=response,
        )
