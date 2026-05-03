"""CodeAgent — a code-focused agent that reads, edits, and commits code."""

import asyncio
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import Field, ConfigDict

from src.message import Message
from src.logger import logger
from src.utils import parse_tool_args
from src.tool.server import tool_manager
from src.skill.server import skill_manager
from src.model import model_manager
from src.registry import AGENT
from src.hook.server import hook_manager
from src.hook.types import HookEvent, HookDecision
from src.agent.types import (
    Agent,
    AgentResponse,
    AgentExtra,
    ThinkOutput,
    AgentContext,
)


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
        workdir: str,
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
            workdir=workdir,
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
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        # Inject a live workdir file snapshot so the agent can see current files
        # without needing to call list_dir_tool just to confirm state.
        workdir = os.path.abspath(ctx.workdir if ctx and ctx.workdir else self.workdir)
        try:
            entries = sorted(os.listdir(workdir))
            lines = []
            for name in entries:
                suffix = "/" if os.path.isdir(os.path.join(workdir, name)) else ""
                lines.append(f"  {name}{suffix}")
            snapshot = "\n".join(lines) if lines else "  (empty)"
        except Exception:
            snapshot = "  (unavailable)"

        base["agent_context"] += f"\n\n### Workdir\n{workdir}\n{snapshot}"
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

        # PRE_STEP
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

                # PRE_ACTION
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

                # POST_ACTION
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

        # POST_STEP
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
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🚀 Starting CodeAgent: {task}")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        # Inject workdir so git_tool and file tools can resolve paths
        if not ctx.workdir:
            ctx.workdir = self.workdir

        if files:
            logger.info(f"| 📂 Attached files: {files}")
            files = await asyncio.gather(*[self._extract_file_content(f) for f in files])
            enhanced_task = await self._generate_enhanced_task(task, files)
        else:
            enhanced_task = task

        task_id = "task_" + datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        # ON_START
        await hook_manager(
            ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": task_id, "task": enhanced_task,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )

        messages = await self._get_messages(enhanced_task, ctx=ctx)

        step_number = 0
        response = {"done": False, "result": None, "reasoning": None}

        while step_number < self.max_steps:
            logger.info(f"| 🔄 Step {step_number+1}/{self.max_steps}")
            response = await self._think_and_action(messages, task_id, step_number, ctx=ctx)
            step_number += 1
            messages = await self._get_messages(enhanced_task, ctx=ctx)
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
            ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": task_id, "result": response.get("result"),
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )

        logger.info(f"| ✅ CodeAgent completed after {step_number}/{self.max_steps} steps")

        return AgentResponse(
            success=response["done"],
            message=response["result"],
            extra=AgentExtra(data=response),
        )
