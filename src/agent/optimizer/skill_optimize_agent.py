"""SkillOptimizeAgent — evolves a skill's SKILL.md given an optimization task."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse, AgentThinkOutput
from src.utils.name_utils import make_id
from src.hook.server import hook_manager
from src.hook.types import HookDecision, HookEvent
from src.logger import logger
from src.message import Message
from src.model import model_manager
from src.registry import AGENT
from src.skill.server import skill_manager
from src.tool.server import tool_manager
from src.utils import parse_tool_args


@AGENT.register_module(force=True)
class SkillOptimizeAgent(Agent):
    """Agent that evolves a skill's SKILL.md to satisfy an optimization task.

    Receives an optimization task from MetaAgent describing how to improve a specific
    skill, reads and edits SKILL.md (and optionally scripts/resources), verifies the
    result, refreshes the skill registry, and reports back via done_tool.
    """

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
            prompt_name=prompt_name or "skill_optimize_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Override: inject skill info into agent context
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
            skill_config = await skill_manager.get_info(target_name)
            lines = [f"- **Skill Name**: {target_name}"]
            if skill_config:
                lines.append(f"- **Description**: {skill_config.description}")
                lines.append(f"- **Version**: {skill_config.version}")
                lines.append(f"- **Type**: {skill_config.skill_type}")
                lines.append(f"- **Skill Directory**: {skill_config.skill_dir}")
                lines.append(f"- **SKILL.md**: {os.path.join(skill_config.skill_dir, 'SKILL.md')}")
                if skill_config.scripts:
                    lines.append("- **Scripts**:")
                    for s in skill_config.scripts:
                        lines.append(f"  - {s}")
                if skill_config.resources:
                    lines.append("- **Resources**:")
                    for r in skill_config.resources:
                        lines.append(f"  - {r}")
                if skill_config.reference_files:
                    lines.append("- **Reference Files**:")
                    for r in skill_config.reference_files:
                        lines.append(f"  - {r}")
            else:
                lines.append("- (skill not found in registry)")
            base["optimization_target"] = "\n".join(lines)
        else:
            base["optimization_target"] = "(no target_name provided)"

        action_errors = kwargs.get("action_errors") or []
        if action_errors:
            error_lines = "\n".join(f"- {e}" for e in action_errors)
            base["agent_context"] += f"\n\n### Previous Step Errors\n{error_lines}"

        return base

    # ------------------------------------------------------------------
    # Core step
    # ------------------------------------------------------------------

    async def _think_and_act(
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
        action_errors = []
        target_name = kwargs.get("target_name")

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id},
            ctx=ctx,
        )

        thinking = ""
        evaluation_previous_goal = ""
        memory = ""
        next_goal = ""

        try:
            think_output = await model_manager(
                name=self.model_name,
                input={"messages": messages, "response_format": AgentThinkOutput},
                ctx=ctx,
            )
            think_output = think_output.extra.parsed_model

            thinking = think_output.thinking
            evaluation_previous_goal = think_output.evaluation_previous_goal
            memory = think_output.memory
            next_goal = think_output.next_goal
            plan_steps = think_output.plan

            logger.info(f"| 💭 Thinking: {thinking}")
            logger.info(f"| 🎯 Next Goal: {next_goal}")
            logger.info(f"| 📋 Plan steps: {len(plan_steps)}")

            for i, step in enumerate(plan_steps):
                action = step.action
                action_type = action.type
                action_name = action.name
                action_args_str = action.args
                action_args = parse_tool_args(action_args_str) if action_args_str else {}

                logger.info(f"| 📝 Step {i+1}/{len(plan_steps)}: {step.description}")
                logger.info(f"| 📝 [{action_type}] {action_name}: {action_args}")

                action_dict = {
                    "index": i,
                    "description": step.description,
                    "type": action_type,
                    "name": action_name,
                    "args": action_args_str,
                    "args_parsed": action_args,
                }

                pre_result = await hook_manager(
                    name="trace_hook",
                    input={"event": HookEvent.PRE_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "task_id": task_id},
                    ctx=ctx,
                )
                if pre_result.decision == HookDecision.BLOCK:
                    logger.warning(f"| 🚫 Action blocked by hook: {pre_result.reason}")
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
                            action_extra = tool_response.extra if hasattr(tool_response, "extra") else None
                            reasoning = action_extra.data.get("reasoning") if action_extra and action_extra.data else None
                            refresh_error = await self._refresh_skill_registry(target_name)
                            if refresh_error:
                                logger.warning(f"| ⚠️ Skill registry refresh failed: {refresh_error}")
                            done = True
                            result = action_result

                except Exception as e:
                    error = str(e)
                    action_errors.append(f"Action '{action_name}' failed: {error}")
                    logger.error(f"| ❌ Action '{action_name}' failed: {e}")

                await hook_manager(
                    name="memory_hook",
                    input={"event": HookEvent.POST_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "action_result": action_result, "task_id": task_id, "error": error},
                    ctx=ctx,
                )
                await hook_manager(
                    name="trace_hook",
                    input={"event": HookEvent.POST_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "action_result": action_result, "task_id": task_id, "error": error},
                    ctx=ctx,
                )

                if done:
                    break

        except Exception as e:
            logger.error(f"| Error in think_and_action: {e}")

        await hook_manager(
            name="memory_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "thinking": thinking, "evaluation_previous_goal": evaluation_previous_goal, "memory": memory, "next_goal": next_goal},
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "thinking": thinking, "evaluation_previous_goal": evaluation_previous_goal, "memory": memory, "next_goal": next_goal},
            ctx=ctx,
        )

        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        target_name: Optional[str] = None,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🚀 Starting SkillOptimizeAgent: {task} (skill={target_name})")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        if not target_name:
            logger.warning("| ⚠️ SkillOptimizeAgent called without target_name")

        task_id = make_id()
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        await hook_manager(
            name="memory_hook",
            input={"event": HookEvent.ON_START, "agent_name": self.name, "task_id": task_id, "task": task, "memory_name": self.memory_name, "use_memory": self.use_memory},
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.ON_START, "agent_name": self.name, "task_id": task_id, "task": task, "memory_name": self.memory_name, "use_memory": self.use_memory},
            ctx=ctx,
        )

        messages = await self._get_messages(task, ctx=ctx, target_name=target_name)

        step_number = 0
        response = {"done": False, "result": None, "reasoning": None, "action_errors": []}

        while step_number < self.max_steps:
            logger.info(f"| 🔄 Step {step_number + 1}/{self.max_steps}")
            response = await self._think_and_act(messages, task_id, step_number, ctx=ctx, target_name=target_name)
            step_number += 1
            action_errors = response.get("action_errors") or []
            messages = await self._get_messages(task, ctx=ctx, target_name=target_name, action_errors=action_errors)
            if response["done"]:
                break

        if step_number >= self.max_steps and not response["done"]:
            logger.warning(f"| 🛑 Reached max steps ({self.max_steps})")
            response = {
                "done": False,
                "result": "Skill optimization did not complete within max steps.",
                "reasoning": "Reached the maximum number of steps.",
            }

        await hook_manager(
            name="memory_hook",
            input={"event": HookEvent.ON_STOP, "agent_name": self.name, "task_id": task_id, "result": response.get("result"), "memory_name": self.memory_name, "use_memory": self.use_memory},
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.ON_STOP, "agent_name": self.name, "task_id": task_id, "result": response.get("result"), "memory_name": self.memory_name, "use_memory": self.use_memory},
            ctx=ctx,
        )

        logger.info(f"| ✅ SkillOptimizeAgent completed after {step_number}/{self.max_steps} steps")

        return AgentResponse(
            success=response["done"],
            message=response["result"] or "",
            extra=AgentExtra(data=response),
        )

    async def _refresh_skill_registry(self, target_name: Optional[str]) -> Optional[str]:
        """Re-register the skill from its directory to pick up SKILL.md edits."""
        if not target_name:
            return None
        try:
            skill_config = await skill_manager.get_info(target_name)
            if skill_config and skill_config.skill_dir:
                await skill_manager.register(skill_dir=skill_config.skill_dir, override=True)
                logger.info(f"| 🔄 Skill '{target_name}' refreshed in registry")
            return None
        except Exception as e:
            return str(e)
