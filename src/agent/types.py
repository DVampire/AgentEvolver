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

class AgentContext(BaseContext):
    """Context passed into agent manager and individual agent instances."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for this agent invocation, useful for tracing and logging.")
    work_dir: Optional[str] = Field(default=None, description="Working directory for file and git tools.")
    extra: Dict[str, Any] = Field(default_factory=dict)

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
    # Path 1: Direct call
    # ------------------------------------------------------------------

    async def __call__(self,
                       task: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None,
                       **kwargs: Any,
                       ) -> "AgentResponse":
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
                       ) -> Optional["AgentResponse"]:
        """Called by the runtime pump when a TaskMessage arrives.

        Default behaviour: delegate to ``__call__`` so that simple actor
        agents only need to implement one method.

        Override to customise event-driven startup (e.g. MetaAgent).
        Return an ``AgentResponse`` to resolve the task immediately; return
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
                      result: "AgentResponse",
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


class AgentExtra(BaseModel):
    """Agent extra data."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    
    file_path: Optional[Union[str, List[str]]] = Field(default=None, description="The file path of the extra data")
    data: Optional[Dict[str, Any]] = Field(default=None, description="The data of the extra data")
    parsed_model: Optional[BaseModel] = Field(default=None, description="The parsed model of the extra data")

class AgentResponse(BaseModel):
    """Agent response."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    
    success: bool = Field(description="Whether the agent has completed the task.")
    message: str = Field(description="The message of the agent.")
    extra: Optional[AgentExtra] = Field(default=None, description="The extra data of the agent.")

__all__ = [
    "InputArgs",
    "AgentConfig",
    "ActionInputArgs",
    "AgentPlanStep",
    "AgentThinkOutput",
    "Agent",
    "AgentResponse",
]
