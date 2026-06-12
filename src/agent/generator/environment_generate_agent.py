"""EnvironmentGenerateAgent — generates a new environment (Python class + config dict) from a description."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext
from src.response.types import Response, ResponseType
from src.hook.server import hook_manager
from src.logger import logger
from src.registry import AGENT


@AGENT.register_module(force=True)
class EnvironmentGenerateAgent(Agent):
    """Agent that generates a new environment from a natural-language description.

    An environment is an action provider (not LLM-driven), so generation produces
    2 files: a Python class under ``src/environment/extended/`` and a config dict
    under ``configs/environments/``. Environments have NO HTML prompt.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="environment_generate_agent")
    description: str = Field(
        default="An agent that generates a new environment Python class and config dict from a description."
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
            prompt_name=prompt_name or "environment_generate_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Override: inject generation target context
    # ------------------------------------------------------------------

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        from src.environment.server import environment_manager

        target_name = kwargs.get("target_name")
        lines = []
        if target_name:
            lines.append(f"- **Requested Environment Name**: `{target_name}`")
            lines.append(f"- **Python Class File**: `src/environment/extended/{target_name}.py`")
            lines.append(f"- **Config File**: `configs/environments/{target_name}.py`")
            existing = await environment_manager.get_info(target_name)
            if existing:
                lines.append(f"- **Status**: already registered (version {existing.version}) — regenerate/overwrite if instructed")
            else:
                lines.append("- **Status**: not yet registered — create from scratch")
        else:
            lines.append("- **Requested Environment Name**: (not specified — infer a snake_case name from the task)")
            lines.append("- **Python Class File**: `src/environment/extended/<inferred_name>.py`")
            lines.append("- **Config File**: `configs/environments/<inferred_name>.py`")

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

        while step_number < self.max_step:
            logger.info(f"| 🔄 [{self.name}] Step {step_number + 1}/{self.max_step}")
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx, target_name=target_name
            )
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response.get("stopped_by_constraint"):
                break

            if response["done"]:
                hook_result = await hook_manager(
                    name="environment_registration_hook",
                    input={
                        "event": HookEvent.ON_STOP,
                        "target_name": target_name,
                        "reasoning": response.get("reasoning") or "",
                        "project_root": get_project_root(),
                    },
                    ctx=ctx,
                )
                if hook_result.decision == HookDecision.BLOCK:
                    response["done"] = False
                    action_errors = [hook_result.reason or "Registration failed."]
                else:
                    break

            messages = await self._get_messages(
                task,
                ctx=ctx,
                target_name=target_name,
                action_errors=action_errors,
                constraint_status=response.get("constraint_status"),
            )

        if step_number >= self.max_step and not response["done"]:
            logger.warning(f"| 🛑 [{self.name}] Reached max steps ({self.max_step})")
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
            success=response["done"] and not response.get("stopped_by_constraint", False),
            message=response["result"] or "",
            data=response,
        )
