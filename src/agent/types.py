"""Agent Context Protocol (agent manager) Types

Core type definitions for the Agent Context Protocol and common Agent
abstractions, aligned with the design of `src.tool.types`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union


from pydantic import BaseModel, ConfigDict, Field

from src.config import config
from src.dynamic import dynamic_manager
from src.logger import logger
from src.memory import memory_manager
from src.message.types import Message
from src.prompt import prompt_manager
from src.tool.server import tool_manager
from src.skill.server import skill_manager
from src.utils import (
    assemble_project_path,
    get_project_root,
)
from src.session import BaseContext
from src.constraint.types import Constraint
from src.registry import CONSTRAINT
from src.response.types import Response, ResponseType

class AgentContext(BaseContext):
    """Context passed into agent manager and individual agent instances."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for this agent invocation, useful for tracing and logging.")
    work_dir: Optional[str] = Field(default=None, description="Working directory for file and git tools.")
    parent_session_id: Optional[str] = Field(default=None, description="ref.name of the parent MetaAgent, used by trace and escalation hooks.")
    subtask_id: Optional[str] = Field(default=None, description="ID of the subtask record in the parent MetaAgent's plan.")

class InputArgs(BaseModel):
    task: str = Field(description="The task to complete.")
    files: Optional[List[str]] = Field(default=None, description="The files to attach to the task.")

class AgentConfig(BaseModel):
    """Agent configuration for registration, similar to `ToolConfig`."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the agent")
    description: str = Field(description="The description of the agent")
    version: str = Field(default="1.0.0", description="Version of the agent")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    require_grad: bool = Field(default=False, description="Whether the agent requires gradients")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")

    cls: Optional[Any] = None
    config: Optional[Dict[str, Any]] = Field(default_factory=dict,description="The initialization configuration of the agent",)
    instance: Optional[Any] = None
    
    code: Optional[str] = Field(default=None, description="Source code for dynamically generated agent classes (used when cls cannot be imported from a module)")

    function_calling: Optional[Dict[str, Any]] = Field(
        default=None, description="Default function calling representation"
    )
    text: Optional[str] = Field(
        default=None, description="Default text representation of the agent"
    )
    args_schema: Optional[Type[BaseModel]] = Field(
        default=None, description="Default args schema (BaseModel type)"
    )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary, recursively serializing nested Pydantic models."""
        
        result = {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "version": self.version,
            "require_grad": self.require_grad,
            
            "permission_mode": self.permission_mode,

            "cls": dynamic_manager.get_class_string(self.cls) if self.cls else None,
            "config": self.config,
            "instance": None,
            "code": self.code,

            "function_calling": self.function_calling,
            "text": self.text,
            "args_schema": dynamic_manager.serialize_args_schema(self.args_schema) if self.args_schema else None,
        }

        return result
    
    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> 'AgentConfig':
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description")
        metadata = data.get("metadata", {})
        version = data.get("version")
        require_grad = data.get("require_grad", False)
        permission_mode = data.get("permission_mode", "workspace_write")

        cls_ = None
        code = data.get("code")
        if code:
            class_name = dynamic_manager.extract_class_name_from_code(code)
            if class_name:
                try:
                    cls_ = dynamic_manager.load_class(
                        code, 
                        class_name=class_name,
                        base_class=Agent,
                        context="agent"
                    )
                except Exception as e:
                    cls_ = None
            else:
                cls_ = None
        else:
            cls_ = None
            
        config = data.get("config", {})
        instance = data.get("instance", None)

        function_calling = data.get("function_calling")
        text = data.get("text")
        _raw_schema = data.get("args_schema")
        args_schema = dynamic_manager.deserialize_args_schema(_raw_schema) if _raw_schema is not None else None
        
        return cls(
            name=name,
            description=description,
            metadata=metadata,
            version=version,
            require_grad=require_grad,
            permission_mode=permission_mode,
            cls=cls_,
            config=config,
            instance=instance,
            function_calling=function_calling,
            text=text,
            args_schema=args_schema,
        )

    def __str__(self) -> str:
        return (
            f"AgentConfig(name={self.name}, "
            f"description={self.description}, "
            f"require_grad={self.require_grad})"
        )

    def __repr__(self) -> str:
        return self.__str__()



class ActionInputArgs(BaseModel):
    type: str = Field(description='The type of this action: "tool", "skill", or "text".')
    name: str = Field(description='The name of the tool, skill, or "text" for plain-text responses.')
    args: str = Field(description='The arguments as a JSON string. Must be a valid JSON object string. e.g., "{\"result\": \"D\", \"reasoning\": \"Step 1: ...\"}"')


class AgentPlanStep(BaseModel):
    """One step in the execution plan — a description plus the single action to run."""
    description: str = Field(description="One-line summary of what this step does.")
    action: ActionInputArgs = Field(description="The single action to execute for this step.")


class AgentThinkOutput(BaseModel):
    """Structured LLM output for plan-based agents.

    The agent outputs only new steps to execute; history is maintained by
    FileSystemMemory. The LLM never re-emits already-executed steps.
    """
    thinking: str = Field(description="Step-by-step reasoning about what to do next.")
    evaluation_previous_goal: str = Field(
        default="",
        description="One sentence: success, failure, or uncertainty of the last step.",
    )
    memory: str = Field(default="", description="1-3 sentences of key facts to remember across steps.")
    next_goal: str = Field(default="", description="One sentence: the immediate next goal.")
    plan: List[AgentPlanStep] = Field(
        default_factory=list,
        description=(
            "New steps to execute next, in order. Each step is exactly one action. "
            "Include done_tool as the last step when the task is complete. "
            "Do not re-emit steps that have already been executed."
        ),
    )


class Agent(BaseModel):
    """Base class for all agents, mirroring the design of `Tool`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the agent.")
    description: str = Field(description="The description of the agent.")
    metadata: Dict[str, Any] = Field(description="The metadata of the agent.")
    version: str = Field(default="1.0.0", description="Version of the agent")
    require_grad: bool = Field(default=False, description="Whether the agent requires gradients")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")

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
        use_memory: bool = True,
        constraints: Optional[List[Constraint]] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        # Set default values
        self.name = name or self.name
        self.description = description or self.description
        self.metadata = metadata or self.metadata
        self.require_grad = require_grad

        # Set working directory
        self.base_dir = base_dir

        # Set prompt name and modules
        self.prompt_name = prompt_name
        self.memory_name = memory_name
        self.use_memory = use_memory
        self.model_name = model_name

        # Setup steps
        self.max_steps = max_steps if max_steps > 0 else int(1e8)
        self.max_actions = max_actions

        self.review_steps = review_steps

        # Runtime constraints — accept Constraint instances or mmengine-style dicts
        # e.g. {"type": "StepConstraint", "max_steps": 20}
        self.constraints: List[Constraint] = self._build_constraints(constraints)

    @staticmethod
    def _build_constraints(raw: Optional[List]) -> List[Constraint]:
        if not raw:
            return []
        result = []
        for item in raw:
            if isinstance(item, Constraint):
                result.append(item)
            elif isinstance(item, dict):
                result.append(CONSTRAINT.build(item))
            else:
                raise TypeError(f"Unsupported constraint type: {type(item)}")
        return result

    async def initialize(self) -> None:
        """Initialize the agent."""
        logger.info(f"| 📁 Agent working directory: {self.base_dir}")

    def __str__(self) -> str:
        return f"Agent(name={self.name}, model={self.model_name}, prompt_name={self.prompt_name})"

    def __repr__(self) -> str:
        return self.__str__()

    async def _get_agent_context(self,
                                 task: str,
                                 step_number: int = 0,
                                 ctx: Optional[AgentContext] = None,
                                 **kwargs) -> Dict[str, Any]:
        """Get the agent context."""
        time_str = datetime.now().isoformat()
        step_info = (
            f"### Step Info\n"
            f"Step {step_number + 1} of {self.max_steps} max possible steps\n"
            f"Current date and time: {time_str}"
        )

        task_section = f"### Task\n{task}"

        history_section = "### Agent History\n[Agent history is disabled.]"
        memory_section = "### Memory\n[Memory is disabled.]"
        if self.use_memory and self.memory_name:
            try:
                memory_info = await memory_manager.get_info(self.memory_name)
                if memory_info and memory_info.instance is not None:
                    session_id = ctx.id if ctx else ""
                    mem_text = await memory_info.instance.get(
                        session_id=session_id,
                        short_term_n=self.review_steps,
                    )
                    if mem_text:
                        memory_section = f"### Memory\n{mem_text}"
                    else:
                        memory_section = "### Memory\n[No memory recorded yet.]"
                    history_section = ""
            except Exception:
                pass

        sections = [task_section, step_info]
        if history_section:
            sections.append(history_section)
        sections.append(memory_section)
        agent_context = "\n\n".join(sections)

        return {"agent_context": agent_context}

    async def _get_tool_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the tool context."""
        contract = await tool_manager.get_contract()
        tool_context = f"### Available Tools\n{contract}" if contract else "### Available Tools\n[No tools loaded.]"
        return {"tool_context": tool_context}

    async def _get_skill_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the skill context from loaded skills via skill manager."""
        skill_content = await skill_manager.get_context()
        skill_context = f"### Available Skills\n{skill_content}" if skill_content else "### Available Skills\n[No skills loaded.]"
        return {"skill_context": skill_context}

    async def _resolve_work_dir(self, ctx: AgentContext, **kwargs) -> str:
        """Resolve the work_dir surfaced in the prompt's `{{ work_dir }}` slot.

        Prefers ctx.work_dir (injected by MetaAgent for sub-agents) over
        self.base_dir so all agents in a MetaAgent run share the same directory.
        """
        return assemble_project_path(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)

    async def _get_messages(self,
                            task: str,
                            ctx: AgentContext,
                            **kwargs) -> List[Message]:
        """Build system+agent messages using prompt templates and context."""

        work_dir = await self._resolve_work_dir(ctx=ctx, **kwargs)
        project_root = get_project_root()
        system_modules = dict(max_actions=self.max_actions, work_dir=work_dir, project_root=project_root)
        agent_message_modules = dict(task=task)
        
        agent_message_modules.update(await self._get_agent_context(task, ctx=ctx, **kwargs))
        agent_message_modules.update(await self._get_tool_context(ctx=ctx))
        agent_message_modules.update(await self._get_skill_context(ctx=ctx))
        
        messages = await prompt_manager.get_messages(
            prompt_name=self.prompt_name,
            system_modules=system_modules,
            agent_modules=agent_message_modules,
        )

        return messages

    # ------------------------------------------------------------------
    # Shared execution loop — all tool-calling agents use this
    # ------------------------------------------------------------------

    async def _think_and_act(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        **kwargs,
    ) -> Dict[str, Any]:
        """One step of the think-and-act loop. Returns done/result/reasoning/action_errors."""
        from src.hook.server import hook_manager
        from src.hook.types import HookDecision, HookEvent
        from src.model import model_manager
        from src.utils import parse_tool_args
        from src.constraint.server import constraint_manager

        done = False
        result = None
        reasoning = None
        action_errors = []

        # --- Constraint checks ---
        if self.constraints:
            # Auto-start session on first step for this task_id
            if not constraint_manager.has_session(task_id):
                constraint_manager.start_session(task_id, self.constraints, self.name)

            violation = await constraint_manager.check(task_id, step_number)
            if violation and violation.violated:
                constraint_manager.end_session(task_id)
                return {"done": True, "result": violation.reason, "reasoning": None, "action_errors": []}

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id},
            ctx=ctx,
        )

        thinking = ""
        evaluation_previous_goal = ""
        next_goal = ""

        try:
            llm_response = await model_manager(
                name=self.model_name,
                input={"messages": messages, "response_format": AgentThinkOutput},
                ctx=ctx,
            )
            # Record token usage for constraint tracking
            if self.constraints and llm_response.usage:
                constraint_manager.record_tokens(task_id, llm_response.usage.total)
            think_output = llm_response.parsed_model

            thinking = think_output.thinking
            evaluation_previous_goal = think_output.evaluation_previous_goal
            next_goal = think_output.next_goal
            plan_steps = think_output.plan

            logger.info(f"| 💭 [{self.name}] Thinking: {thinking}")
            logger.info(f"| 🎯 [{self.name}] Next Goal: {next_goal}")
            logger.info(f"| 📋 [{self.name}] Plan steps: {len(plan_steps)}")

            for i, step in enumerate(plan_steps):
                action = step.action
                action_type = action.type
                action_name = action.name
                action_args_str = action.args
                action_args = parse_tool_args(action_args_str) if action_args_str else {}

                logger.info(f"| 📝 [{self.name}] Step {i+1}/{len(plan_steps)}: {step.description}")
                logger.info(f"| 📝 [{self.name}] [{action_type}] {action_name}: {action_args}")

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
                    logger.warning(f"| 🚫 [{self.name}] Action blocked by hook: {pre_result.reason}")
                    continue

                action_result = None
                error = None

                try:
                    if action_type == "text":
                        action_result = action_args.get("content", "")
                        logger.info(f"| 💬 [{self.name}] Text: {str(action_result)}")

                    elif action_type == "skill":
                        response = await skill_manager(name=action_name, input=action_args, ctx=ctx)
                        action_result = response.message
                        logger.info(f"| ✅ [{self.name}] Skill '{action_name}' completed (success={response.success})")

                    else:
                        tool_response = await tool_manager(name=action_name, input=action_args, ctx=ctx)
                        action_result = tool_response.message
                        logger.info(f"| ✅ [{self.name}] Tool '{action_name}' completed")

                        if action_name == "done_tool":
                            done = True
                            result = action_result
                            reasoning = (tool_response.data or {}).get("reasoning") if hasattr(tool_response, "data") else None

                except Exception as e:
                    error = str(e)
                    action_errors.append(f"Action '{action_name}' failed: {error}")
                    logger.error(f"| ❌ [{self.name}] Action '{action_name}' failed: {e}")

                await hook_manager(
                    name="memory_hook",
                    input={"event": HookEvent.POST_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "action_result": action_result, "task_id": task_id, "error": error, "use_memory": self.use_memory, "memory_name": self.memory_name},
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
            logger.error(f"| ❌ [{self.name}] Error in _think_and_act: {e}")

        await hook_manager(
            name="memory_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "thinking": thinking, "evaluation_previous_goal": evaluation_previous_goal, "next_goal": next_goal, "use_memory": self.use_memory, "memory_name": self.memory_name},
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "thinking": thinking, "evaluation_previous_goal": evaluation_previous_goal, "next_goal": next_goal},
            ctx=ctx,
        )

        # Clean up constraint session when task finishes
        if done and self.constraints:
            constraint_manager.end_session(task_id)

        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors}

    # ------------------------------------------------------------------
    # Path 1: Direct call
    # ------------------------------------------------------------------

    async def __call__(self,
                       task: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None,
                       **kwargs: Any,
                       ) -> "Response":
        """Direct invocation — no runtime machinery involved.

        Subclasses **must** override this to implement their task logic.
        This is the only method simple actor agents need to implement.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement __call__"
        )

    # ------------------------------------------------------------------
    # Path 2: Event-driven (runtime / mailbox)
    # ------------------------------------------------------------------

    async def on_start(self,
                       task: str,
                       files: Optional[List[str]],
                       ctx: Optional[AgentContext],
                       ref: Any,
                       **kwargs: Any,
                       ) -> Optional["Response"]:
        """Called by the runtime pump when a TaskMessage arrives.

        Default behaviour: delegate to ``__call__`` so that simple actor
        agents only need to implement one method.

        Override to customise event-driven startup (e.g. MetaAgent).
        Return a ``Response`` to resolve the task immediately; return
        ``None`` to signal that resolution will happen asynchronously via
        a later ``on_event`` / ``on_stop`` call.
        """
        return await self.__call__(task=task, files=files, ctx=ctx, **kwargs)

    async def on_event(self,
                       msg: Any,
                       ref: Any,
                       ) -> None:
        """Called by the runtime pump for every non-TaskMessage message.

        Default behaviour: no-op.  Event-driven agents (e.g. MetaAgent)
        override this to handle SubtaskDone, Escalation, etc.
        """

    async def on_stop(self,
                      result: "Response",
                      ctx: Optional[AgentContext],
                      ) -> None:
        """Called after the task resolves — cleanup / teardown hook.

        For sync agents this is called automatically by ``handle()`` once
        ``on_start`` returns a result.  Async / event-driven agents should
        call this themselves when they are done (e.g. inside ``_finish``).

        Default behaviour: no-op.  Override to emit ON_STOP hooks, flush
        memory, reset per-invocation state, etc.
        """

    # ------------------------------------------------------------------
    # Framework dispatcher — do NOT override in subclasses
    # ------------------------------------------------------------------

    async def handle(self, msg: Any, ref: Any) -> None:
        """Runtime pump dispatcher.

        Routes each inbox message to the appropriate lifecycle method:
          * TaskMessage          → on_start → [on_stop if resolved]
          * Everything else      → on_event

        This method is part of the framework layer.  Subclasses should
        implement ``__call__``, ``on_start``, ``on_event``, and ``on_stop``
        instead of overriding ``handle``.
        """
        from src.runtime.types import TaskMessage
        if isinstance(msg, TaskMessage):
            ctx = msg.kwargs.get("ctx")
            ref._pending_reply = msg.reply_future      # hand ownership to ref
            try:
                extra_kwargs = {k: v for k, v in msg.kwargs.items() if k not in ("ctx", "files")}
                result = await self.on_start(
                    task=msg.task or "",
                    files=msg.kwargs.get("files"),
                    ctx=ctx,
                    ref=ref,
                    **extra_kwargs,
                )
                if result is not None:
                    if ref._pending_reply is not None and not ref._pending_reply.done():
                        ref._pending_reply.set_result(result)
                        ref._pending_reply = None
                    await self.on_stop(result, ctx)
            except asyncio.CancelledError:
                if ref._pending_reply is not None and not ref._pending_reply.done():
                    ref._pending_reply.cancel()
                raise
            except Exception as exc:
                logger.error(f"| ❌ {self.name} task failed: {exc}", exc_info=True)
                if ref._pending_reply is not None and not ref._pending_reply.done():
                    ref._pending_reply.set_exception(exc)
        else:
            await self.on_event(msg, ref)


__all__ = [
    "InputArgs",
    "AgentConfig",
    "ActionInputArgs",
    "AgentPlanStep",
    "AgentThinkOutput",
    "Agent",
    "AgentContext",
]
