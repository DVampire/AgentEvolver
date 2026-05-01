"""CodeAgent — a code-focused agent that reads, edits, and commits code."""

import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import Field, ConfigDict

from src.message import Message
from src.logger import logger
from src.utils import parse_tool_args
from src.tool.server import tool_manager
from src.skill.server import skill_manager
from src.memory import memory_manager, EventType
from src.model import model_manager
from src.registry import AGENT
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

    async def _get_git_status(self, workdir: str) -> str:
        """Return a one-line git status summary for the step info block."""
        try:
            tool = await tool_manager.get("git_tool")
            if tool is None:
                return ""

            class _FakeCtx:
                pass

            fake_ctx = _FakeCtx()
            fake_ctx.workdir = workdir

            response = await tool(action="status", ctx=fake_ctx)
            if response.success and response.message:
                lines = [l for l in response.message.splitlines() if l.strip()]
                changed = [l for l in lines if l.startswith("\t") or l.startswith("modified") or "Changes" in l]
                return "\n".join(lines[:8]) if lines else "(clean)"
        except Exception:
            pass
        return ""

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Extend base agent context with git status."""
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        workdir = self.workdir
        git_status = await self._get_git_status(workdir)
        if git_status:
            base["agent_context"] = base["agent_context"].replace(
                "### Step Info",
                "### Step Info",
                1,
            )
            # Append git status right after the step info block
            step_marker = "### Agent History"
            base["agent_context"] = base["agent_context"].replace(
                step_marker,
                f"### Git Status\n{git_status}\n\n{step_marker}",
                1,
            )

        return base

    async def _think_and_action(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: AgentContext,
        **kwargs,
    ) -> Dict[str, Any]:
        """Think and execute actions for one step."""
        done = False
        result = None
        reasoning = None

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

                if action_type == "text":
                    action_result = action_args.get("content", "")
                    logger.info(f"| 💬 Text: {str(action_result)}")
                    action_dict = action.model_dump()
                    action_dict["output"] = action_result
                    action_results.append(action_dict)

                elif action_type == "skill":
                    response = await skill_manager(
                        name=action_name,
                        input=action_args,
                        ctx=ctx,
                    )
                    action_result = response.message
                    logger.info(f"| ✅ Skill '{action_name}' completed (success={response.success})")
                    action_dict = action.model_dump()
                    action_dict["output"] = action_result
                    action_results.append(action_dict)

                else:
                    tool_response = await tool_manager(
                        name=action_name,
                        input=action_args,
                        ctx=ctx,
                    )
                    action_result = tool_response.message
                    logger.info(f"| ✅ Tool '{action_name}' completed")
                    action_dict = action.model_dump()
                    action_dict["output"] = action_result
                    action_results.append(action_dict)

                    if action_name == "done_tool":
                        done = True
                        result = action_result
                        action_extra = tool_response.extra if hasattr(tool_response, "extra") else None
                        reasoning = action_extra.data.get("reasoning") if action_extra and action_extra.data else None
                        break

            event_data = {
                "thinking": thinking,
                "evaluation_previous_goal": evaluation_previous_goal,
                "memory": memory,
                "next_goal": next_goal,
                "actions": action_results,
            }

            if self.use_memory and self.memory_name:
                await memory_manager.add_event(
                    memory_name=self.memory_name,
                    step_number=step_number,
                    event_type=EventType.ACTION_STEP,
                    data=event_data,
                    agent_name=self.name,
                    task_id=task_id,
                    ctx=ctx,
                )

        except Exception as e:
            logger.error(f"| Error in think_and_action: {e}")

        return {"done": done, "result": result, "reasoning": reasoning}

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> AgentResponse:
        """Run the code agent on the given task."""
        logger.info(f"| 🚀 Starting CodeAgent: {task}")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        if files:
            logger.info(f"| 📂 Attached files: {files}")
            files = await asyncio.gather(*[self._extract_file_content(f) for f in files])
            enhanced_task = await self._generate_enhanced_task(task, files)
        else:
            enhanced_task = task

        task_id = "task_" + datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        if self.use_memory and self.memory_name:
            await memory_manager.start_session(memory_name=self.memory_name, ctx=ctx)
            await memory_manager.add_event(
                memory_name=self.memory_name,
                step_number=0,
                event_type=EventType.TASK_START,
                data=dict(task=enhanced_task),
                agent_name=self.name,
                task_id=task_id,
                ctx=ctx,
            )
        else:
            logger.info(f"| ⏭️ Memory disabled, skipping session management")

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

        if self.use_memory and self.memory_name:
            await memory_manager.add_event(
                memory_name=self.memory_name,
                step_number=step_number,
                event_type=EventType.TASK_END,
                data=response,
                agent_name=self.name,
                task_id=task_id,
                ctx=ctx,
            )
            await memory_manager.end_session(memory_name=self.memory_name, ctx=ctx)

        logger.info(f"| ✅ CodeAgent completed after {step_number}/{self.max_steps} steps")

        return AgentResponse(
            success=response["done"],
            message=response["result"],
            extra=AgentExtra(data=response),
        )
