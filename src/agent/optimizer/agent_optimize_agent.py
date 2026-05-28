"""AgentOptimizeAgent — evolves a generated agent (Python class and/or HTML prompt) given an optimization task."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.registry import AGENT
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class AgentOptimizeAgent(Agent):
    """Agent that evolves a generated agent to satisfy an optimization task.

    Can modify the Python class file, the HTML prompt file, or both.
    After successful edits, reloads the agent class and re-registers it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="agent_optimize_agent")
    description: str = Field(
        default="An agent that evolves a generated agent (Python class and/or HTML prompt) given an optimization task."
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
            prompt_name=prompt_name or "agent_optimize_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )
        self.project_root = str(Path(__file__).resolve().parents[3])

    # ------------------------------------------------------------------
    # Override: inject optimization target context
    # ------------------------------------------------------------------

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        from src.agent.server import agent_manager

        target_name = kwargs.get("target_name")
        if target_name:
            agent_config = await agent_manager.get_info(target_name)
            py_path = os.path.join(self.project_root, "src", "agent", "extended", f"{target_name}.py")
            html_path = os.path.join(self.project_root, "src", "prompt", "default", f"{target_name}.html")

            lines = [f"- **Agent Name**: `{target_name}`"]
            if agent_config:
                lines.append(f"- **Description**: {agent_config.description}")
                lines.append(f"- **Version**: {agent_config.version}")
            else:
                lines.append("- (agent not found in registry)")
            lines.append(f"- **Python File**: `{py_path}`")
            lines.append(f"- **Python File Exists**: {os.path.exists(py_path)}")
            lines.append(f"- **HTML Prompt File**: `{html_path}`")
            lines.append(f"- **HTML Prompt Exists**: {os.path.exists(html_path)}")
            lines.append(f"- **Agent Type**: {'tool_calling' if os.path.exists(html_path) else 'workflow'}")

            base["optimization_target"] = "\n".join(lines)
        else:
            base["optimization_target"] = "(no target_name provided)"

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
    ) -> AgentResponse:
        logger.info(f"| 🚀 Starting {self.name}: {task} (agent={target_name})")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        if not target_name:
            logger.warning(f"| ⚠️ {self.name} called without target_name")
            return AgentResponse(success=False, message="target_name is required for optimization but was not provided.")

        from src.agent.server import agent_manager
        agent_config = await agent_manager.get_info(target_name)
        if agent_config is None:
            logger.warning(f"| ⚠️ Agent '{target_name}' not found in registry, refusing optimization")
            return AgentResponse(success=False, message=f"Agent '{target_name}' not found in registry.")
        if not agent_config.require_grad:
            logger.warning(f"| ⚠️ Agent '{target_name}' has require_grad=False, refusing optimization")
            return AgentResponse(success=False, message=f"Agent '{target_name}' is not evolvable (require_grad=False).")

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
                from src.utils import get_project_root
                await hook_manager(
                    name="agent_registration_hook",
                    input={
                        "target_name": target_name,
                        "reasoning": response.get("reasoning") or "",
                        "project_root": get_project_root(),
                        "model_name": self.model_name,
                    },
                    ctx=ctx,
                )
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

        return AgentResponse(
            success=response["done"],
            message=response["result"] or "",
            extra=AgentExtra(data=response),
        )

