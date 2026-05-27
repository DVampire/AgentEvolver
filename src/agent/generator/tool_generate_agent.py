"""ToolGenerateAgent — an agent that generates new tool source code from a description."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse, AgentThinkOutput
from src.utils.name_utils import make_id
from src.hook.server import hook_manager
from src.hook.types import HookContext, HookDecision, HookEvent
from src.config import config
from src.logger import logger
from src.message import Message
from src.model import model_manager
from src.prompt import prompt_manager
from src.registry import AGENT
from src.skill.server import skill_manager
from src.tool.server import tool_manager
from src.dynamic import dynamic_manager
from src.utils import parse_tool_args


def _load_class_from_file(file_path: str, module_name: Optional[str] = None):
    """Load a class from a .py file using importlib, bypassing string exec.

    Uses Python's standard import machinery so the file is parsed and compiled
    normally — no JSON-escaped-quote issues that plague exec(source_string).
    """
    import sys
    import importlib.util

    mod_name = module_name or f"_dynamic_{id(file_path)}"
    # Evict any stale cached module so we always load fresh from disk.
    sys.modules.pop(mod_name, None)

    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    # Return the first class defined in the module that isn't imported.
    for attr in vars(module).values():
        if isinstance(attr, type) and attr.__module__ == mod_name:
            return attr

    raise ValueError(f"No class found in {file_path}")


@AGENT.register_module(force=True)
class ToolGenerateAgent(Agent):
    """Agent that generates a new tool from a natural-language description.

    Receives a generation task from MetaAgent describing what the new tool should do,
    writes a new .py file under src/tool/extended/, verifies it, registers it, and
    reports back via done_tool.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="tool_generate_agent")
    description: str = Field(
        default="An agent that generates new tool source code from a description."
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
            prompt_name=prompt_name or "tool_generate_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_steps=max_steps,
            review_steps=review_steps,
            require_grad=require_grad,
            **kwargs,
        )
        # project_root: two levels up from this file (src/agent/generator/ → repo root)
        from pathlib import Path
        self.project_root = str(Path(__file__).resolve().parents[3])

    # ------------------------------------------------------------------
    # Override: inject project_root into system modules
    # ------------------------------------------------------------------

    async def _get_messages(self, task: str, ctx, **kwargs):
        work_dir = await self._resolve_work_dir(ctx=ctx, **kwargs)
        system_modules = dict(
            max_actions=self.max_actions,
            work_dir=work_dir,
            project_root=self.project_root,
        )
        agent_message_modules = dict(task=task)
        agent_message_modules.update(await self._get_agent_context(task, ctx=ctx, **kwargs))
        agent_message_modules.update(await self._get_tool_context(ctx=ctx))
        agent_message_modules.update(await self._get_skill_context(ctx=ctx))

        messages = await prompt_manager.get_messages(
            prompt_name=self.prompt_name,
            system_modules=system_modules,
            agent_modules=agent_message_modules,
        )

        hook_ctx = HookContext(
            event=HookEvent.PRE_MESSAGES,
            id=ctx.id,
            agent_name=self.name,
            messages=messages,
            max_tokens=getattr(config, "max_tokens", 0),
        )
        result = await hook_manager(hook_ctx)
        messages = result.modified_messages if result.modified_messages is not None else messages
        if result.additional_context:
            from src.message import SystemMessage
            messages = list(messages) + [SystemMessage(content=result.additional_context)]

        return messages

    # ------------------------------------------------------------------
    # Override: inject generation context
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
        lines = []
        if target_name:
            lines.append(f"- **Requested Tool Name**: `{target_name}`")
            lines.append(f"- **Target File**: `src/tool/extended/{target_name}.py`")
            existing = await tool_manager.get_info(target_name)
            if existing:
                lines.append(f"- **Status**: already registered (version {existing.version}) — regenerate/overwrite if instructed")
            else:
                lines.append("- **Status**: not yet registered — create from scratch")
        else:
            lines.append("- **Requested Tool Name**: (not specified — infer a snake_case name from the task)")
            lines.append("- **Target File**: `src/tool/extended/<inferred_name>.py`")

        base["generation_target"] = "\n".join(lines)

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
                response_format=AgentThinkOutput,
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
                            action_extra = tool_response.extra if hasattr(tool_response, "extra") else None
                            reasoning = action_extra.data.get("reasoning") if action_extra and action_extra.data else None
                            reg_error = await self._try_register_tool(target_name, reasoning or "")
                            if reg_error is None:
                                done = True
                                result = action_result
                            else:
                                action_result = (
                                    f"[registration failed] {reg_error}\n"
                                    "Please fix the code at the reported location and call done_tool again."
                                )

                except Exception as e:
                    error = str(e)
                    action_errors.append(f"Action '{action_name}' failed: {error}")
                    logger.error(f"| ❌ Action '{action_name}' failed: {e}")

                await hook_manager(
                    ctx, HookEvent.POST_ACTION,
                    agent_name=self.name,
                    step_number=step_number,
                    action=action_dict,
                    action_result=action_result,
                    extra={"task_id": task_id, "error": error},
                )

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

        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def _run(
        self,
        task: str,
        target_name: Optional[str] = None,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🚀 Starting ToolGenerateAgent: {task} (target_name={target_name})")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        if not ctx.work_dir:
            ctx.work_dir = self.base_dir

        task_id = make_id()
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        await hook_manager(
            ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": task_id, "task": task, "target_name": target_name,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
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
                "result": "Tool generation did not complete within max steps.",
                "reasoning": "Reached the maximum number of steps.",
            }

        await hook_manager(
            ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": task_id, "result": response.get("result"),
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )

        logger.info(f"| ✅ ToolGenerateAgent completed after {step_number}/{self.max_steps} steps")

        return AgentResponse(
            success=response["done"],
            message=response["result"] or "",
            extra=AgentExtra(data=response),
        )

    async def _try_register_tool(self, target_name: Optional[str], reasoning: str) -> Optional[str]:
        """Try to load and register the generated tool. Returns None on success, error string on failure."""
        import os

        generated_path = None
        for token in reasoning.split():
            if token.startswith("src/tool/extended/") and token.endswith(".py"):
                generated_path = os.path.join(self.project_root, token)
                break

        if not generated_path and target_name:
            generated_path = os.path.join(
                self.project_root,
                f"src/tool/extended/{target_name}.py",
            )

        if not generated_path or not os.path.exists(generated_path):
            return f"Could not locate generated tool file (expected src/tool/extended/{target_name}.py)"

        try:
            new_cls = _load_class_from_file(generated_path, target_name)
            with open(generated_path, "r") as f:
                new_code = f.read()
            inferred_name = target_name or getattr(new_cls, "name", None) or new_cls.__name__
            await tool_manager.register(tool=new_cls, config={}, code=new_code, override=True)
            logger.info(f"| 🔄 Tool '{inferred_name}' registered from {generated_path}")
            return None
        except Exception as e:
            logger.warning(f"| ⚠️ Registration attempt failed: {e}")
            return str(e)
