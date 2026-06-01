"""CodeAgent — a code-focused agent that reads, edits, and commits code."""

import os
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
            prompt_name=prompt_name or "code_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
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

        # Inject a live workdir file snapshot so the agent can see current files
        # without needing to call list_dir_tool just to confirm state.
        work_dir = os.path.abspath(
            ctx.work_dir if ctx and ctx.work_dir else self.base_dir
        )
        try:
            entries = sorted(os.listdir(work_dir))
            lines = []
            for name in entries:
                suffix = "/" if os.path.isdir(os.path.join(work_dir, name)) else ""
                lines.append(f"  {name}{suffix}")
            snapshot = "\n".join(lines) if lines else "  (empty)"
        except Exception:
            snapshot = "  (unavailable)"

        base["agent_context"] += f"\n\n### Work Dir\n{work_dir}\n{snapshot}"

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

        messages = await self._get_messages(enhanced_task, ctx=ctx)

        step_number = 0
        response = {
            "done": False,
            "result": None,
            "reasoning": None,
            "action_errors": [],
        }

        while step_number < self.max_steps:
            logger.info(f"| 🔄 Step {step_number+1}/{self.max_steps}")
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx
            )
            step_number += 1
            action_errors = response.get("action_errors") or []
            messages = await self._get_messages(
                enhanced_task, ctx=ctx, action_errors=action_errors
            )
            if response["done"]:
                break

        if step_number >= self.max_steps and not response["done"]:
            logger.warning(f"| 🛑 Reached max steps ({self.max_steps})")
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
            f"| ✅ CodeAgent completed after {step_number}/{self.max_steps} steps"
        )

        return Response(type=ResponseType.AGENT, 
            success=response["done"],
            message=response["result"],
            data=response,
        )
