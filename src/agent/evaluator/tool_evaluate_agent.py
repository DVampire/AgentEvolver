"""ToolEvaluateAgent — an agent that evaluates tool behavior given an evaluation task."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse, ThinkOutput
from src.hook.server import hook_manager
from src.hook.types import HookDecision, HookEvent
from src.logger import logger
from src.message import Message
from src.model import model_manager
from src.registry import AGENT
from src.skill.server import skill_manager
from src.tool.server import tool_manager
from src.dynamic import dynamic_manager
from src.utils import parse_tool_args
from src.agent.generator.tool_generate_agent import _load_class_from_file


@AGENT.register_module(force=True)
class ToolEvaluateAgent(Agent):
    """Agent that evaluates tool behavior against an evaluation task.

    Receives an evaluation task from MetaAgent, reads the target tool's source
    file and runs it against test cases using bash/python tools, collects results,
    and reports a structured evaluation verdict via done_tool.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="tool_evaluate_agent")
    description: str = Field(
        default="An agent that evaluates tool behavior given an evaluation task."
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
        max_steps: int = 20,
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
            prompt_name=prompt_name or "tool_evaluate_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Override: inject tool info into agent context
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
            base["evaluation_target"] = "\n".join(lines)
        else:
            base["evaluation_target"] = "(no target_name provided)"

        return base

    # ------------------------------------------------------------------
    # Core step
    # ------------------------------------------------------------------

    async def _think_and_action(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: AgentContext,
        **kwargs,
    ) -> Dict[str, Any]:
        done = False
        result = None
        reasoning = None

        await hook_manager(
            ctx, HookEvent.PRE_STEP,
            agent_name=self.name,
            step_number=step_number,
            extra={"task_id": task_id},
        )

        thinking = ""
        evaluation_previous_goal = ""
        memory = ""
        next_goal = ""

        try:
            think_output = await model_manager(
                model=self.model_name,
                messages=messages,
                response_format=ThinkOutput,
            )
            think_output = think_output.extra.parsed_model

            thinking = think_output.thinking
            evaluation_previous_goal = think_output.evaluation_previous_goal
            memory = think_output.memory
            next_goal = think_output.next_goal
            actions = think_output.actions

            if step_number == 0 and think_output.initial_plan:
                await hook_manager(
                    ctx, HookEvent.ON_CUSTOM,
                    agent_name=self.name,
                    extra={"meta_type": "plan_init", "items": [
                        {"id": i.id, "description": i.description, "status": i.status}
                        for i in think_output.initial_plan
                    ]},
                )
                logger.info(f"| 📋 Plan initialized: {len(think_output.initial_plan)} steps")
            if think_output.plan_updates:
                await hook_manager(
                    ctx, HookEvent.ON_CUSTOM,
                    agent_name=self.name,
                    extra={"meta_type": "plan_update", "updates": [
                        {"id": u.id, "status": u.status}
                        for u in think_output.plan_updates
                    ]},
                )

            logger.info(f"| 💭 Thinking: {thinking}")
            logger.info(f"| 🎯 Next Goal: {next_goal}")
            logger.info(f"| 🔧 Actions: {actions}")

            action_results = []

            for i, action in enumerate(actions):
                action_type = action.type
                action_name = action.name
                action_args_str = action.args
                action_args = parse_tool_args(action_args_str) if action_args_str else {}

                logger.info(f"| 📝 Action {i+1}/{len(actions)}: [{action_type}] {action_name}")
                logger.info(f"| 📝 Args: {action_args}")

                action_dict = {
                    "index": i,
                    "type": action_type,
                    "name": action_name,
                    "args": action_args_str,
                    "args_parsed": action_args,
                }

                pre_result = await hook_manager(
                    ctx, HookEvent.PRE_ACTION,
                    agent_name=self.name,
                    step_number=step_number,
                    action=action_dict,
                    extra={"task_id": task_id},
                )
                if pre_result.decision == HookDecision.BLOCK:
                    logger.warning(f"| 🚫 Action blocked by hook: {pre_result.reason}")
                    action_results.append({**action_dict, "output": f"[blocked] {pre_result.reason}"})
                    continue

                action_result = None
                error = None

                try:
                    if action_type == "text":
                        action_result = action_args.get("content", "")
                        logger.info(f"| 💬 Text: {str(action_result)}")

                    elif action_type == "skill":
                        response = await skill_manager(
                            name=action_name,
                            input=action_args,
                            ctx=ctx,
                        )
                        action_result = response.message
                        logger.info(f"| ✅ Skill '{action_name}' completed (success={response.success})")

                    else:
                        tool_response = await tool_manager(
                            name=action_name,
                            input=action_args,
                            ctx=ctx,
                        )
                        action_result = tool_response.message
                        logger.info(f"| ✅ Tool '{action_name}' completed")

                        if action_name == "done_tool":
                            done = True
                            result = action_result
                            action_extra = tool_response.extra if hasattr(tool_response, "extra") else None
                            reasoning = action_extra.data.get("reasoning") if action_extra and action_extra.data else None

                except Exception as e:
                    error = str(e)
                    logger.error(f"| ❌ Action '{action_name}' failed: {e}")

                await hook_manager(
                    ctx, HookEvent.POST_ACTION,
                    agent_name=self.name,
                    step_number=step_number,
                    action=action_dict,
                    action_result=action_result,
                    extra={"task_id": task_id, "error": error},
                )

                action_dict["output"] = action_result
                action_results.append(action_dict)

                if done:
                    break

        except Exception as e:
            logger.error(f"| Error in think_and_action: {e}")

        await hook_manager(
            ctx, HookEvent.POST_STEP,
            agent_name=self.name,
            step_number=step_number,
            extra={"task_id": task_id, "thinking": thinking, "evaluation_previous_goal": evaluation_previous_goal, "memory": memory, "next_goal": next_goal},
        )

        return {"done": done, "result": result, "reasoning": reasoning}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        target_name: str,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🚀 Starting ToolEvaluateAgent: {task} (tool={target_name})")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()


        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        tool_config = await tool_manager.get_info(target_name)
        if tool_config is None:
            tool_config = await self._try_load_tool(target_name)
        if tool_config is None:
            logger.warning(f"| ⚠️ Tool '{target_name}' not found in registry, refusing evaluation")
            return AgentResponse(success=False, message=f"Tool '{target_name}' not found in registry.")

        task_id = "eval_" + datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        await hook_manager(
            ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": task_id, "task": task, "target_name": target_name,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )

        messages = await self._get_messages(task, ctx=ctx, target_name=target_name)

        step_number = 0
        response = {"done": False, "result": None, "reasoning": None}

        while step_number < self.max_steps:
            logger.info(f"| 🔄 Step {step_number + 1}/{self.max_steps}")
            response = await self._think_and_action(messages, task_id, step_number, ctx=ctx)
            step_number += 1
            messages = await self._get_messages(task, ctx=ctx, target_name=target_name)
            if response["done"]:
                break

        if step_number >= self.max_steps and not response["done"]:
            logger.warning(f"| 🛑 Reached max steps ({self.max_steps})")
            response = {
                "done": False,
                "result": "Tool evaluation did not complete within max steps.",
                "reasoning": "Reached the maximum number of steps.",
            }

        await hook_manager(
            ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": task_id, "result": response.get("result"),
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )

        logger.info(f"| ✅ ToolEvaluateAgent completed after {step_number}/{self.max_steps} steps")

        return AgentResponse(
            success=response["done"],
            message=response["result"] or "",
            extra=AgentExtra(data=response),
        )

    async def _try_load_tool(self, target_name: str):
        """Try to load and register a tool from src/tool/extended/ if not in registry."""
        import os
        from src.utils import assemble_project_path

        tool_path = str(assemble_project_path(f"src/tool/extended/{target_name}.py"))
        if not os.path.exists(tool_path):
            return None
        try:
            new_cls = _load_class_from_file(tool_path, target_name)
            with open(tool_path, "r") as f:
                code = f.read()
            await tool_manager.register(tool=new_cls, config={}, code=code, override=True)
            logger.info(f"| 🔄 Tool '{target_name}' auto-loaded from {tool_path}")
            return await tool_manager.get_info(target_name)
        except Exception as e:
            logger.warning(f"| ⚠️ Auto-load of '{target_name}' failed: {e}")
            return None
