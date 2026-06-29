"""AgentGenerateAgent — generates a new agent (Python class + optional HTML prompt) from a description."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext
from src.response.types import Response, ResponseType
from src.hook.server import hook_manager
from src.logger import logger
from src.registry import AGENT


@AGENT.register_module(force=True)
class AgentGenerateAgent(Agent):
    """Agent that generates a new agent (Python class + optional HTML prompt) from a natural-language description.

    For tool-calling agents: generates Python class + HTML prompt + config dict (3 files).
    For workflow agents: generates Python class + config dict (2 files, no prompt needed).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="agent_generate_agent")
    description: str = Field(
        default="An agent that generates a new agent Python class and optional HTML prompt from a description."
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
            prompt_name=prompt_name or "agent_generate_agent",
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

        from src.agent.server import agent_manager

        target_name = kwargs.get("target_name")
        lines = []
        if target_name:
            lines.append(f"- **Requested Agent Name**: `{target_name}`")
            lines.append(f"- **Python Class File**: `extension/agent/{target_name}.py`")
            lines.append(f"- **HTML Prompt File**: `extension/prompt/{target_name}.html` (tool-calling only)")
            lines.append(f"- **Config File**: `configs/agents/{target_name}.py`")
            existing = await agent_manager.get_info(target_name)
            if existing:
                lines.append(f"- **Status**: already registered (version {existing.version}) — regenerate/overwrite if instructed")
            else:
                lines.append("- **Status**: not yet registered — create from scratch")
        else:
            lines.append("- **Requested Agent Name**: (not specified — infer a snake_case name from the task)")
            lines.append("- **Python Class File**: `extension/agent/<inferred_name>.py`")
            lines.append("- **HTML Prompt File**: `extension/prompt/<inferred_name>.html` (tool-calling only)")
            lines.append("- **Config File**: `configs/agents/<inferred_name>.py`")

        base["generation_target"] = "\n".join(lines)

        # Live workspace listing (see workspace sub-module in agent_generate_agent.html).
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
                task,
                ctx=ctx,
                target_name=target_name,
                step_number=step_number,
                action_errors=action_errors,
                constraint_status=constraint_status,
            )
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx, target_name=target_name
            )
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response["done"]:
                hook_result = await hook_manager(
                    name="agent_registration_hook",
                    input={
                        "event": HookEvent.ON_STOP,
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
