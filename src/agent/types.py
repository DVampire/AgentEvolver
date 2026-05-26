"""Agent Context Protocol (agent manager) Types

Core type definitions for the Agent Context Protocol and common Agent
abstractions, aligned with the design of `src.tool.types`.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union


import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from src.config import config
from src.hook.server import hook_manager
from src.hook.types import HookContext, HookEvent
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
    agent_name: str = Field(default="", description="Name of the agent being called.")
    task_id: Optional[str] = Field(default=None, description="Task identifier for tracing.")
    parent_agent: Optional[str] = Field(default=None, description="Name of the parent agent if this is a sub-agent call.")
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


def format_actions(actions: List[BaseModel]) -> str:
    """Format actions (tool/skill calls) as a Markdown table using pandas."""
    rows = []
    for action in actions:
        if isinstance(action.args, dict):
            args_str = ", ".join(f"{k}={v}" for k, v in action.args.items())
        else:
            args_str = str(action.args)

        rows.append({
            "Type": action.type if hasattr(action, "type") else "tool",
            "Name": action.name,
            "Args": args_str,
            "Output": action.output if hasattr(action, "output") and action.output is not None else None,
        })

    df = pd.DataFrame(rows)

    if df["Output"].isna().all():
        df = df.drop(columns=["Output"])
    else:
        df["Output"] = df["Output"].fillna("None")

    return df.to_markdown(index=True)


class ActionInputArgs(BaseModel):
    type: str = Field(description='The type of this action: "tool", "skill", or "text".')
    name: str = Field(description='The name of the tool, skill, or "text" for plain-text responses.')
    args: str = Field(description='The arguments as a JSON string. Must be a valid JSON object string. e.g., "{\"result\": \"D\", \"reasoning\": \"Step 1: ...\"}"')


class PlanItem(BaseModel):
    id: str = Field(description='Short step ID like "step-1", "step-2".')
    description: str = Field(description="What this step involves.")
    status: str = Field(default="pending", description="pending | in_progress | done | failed")


class TodoUpdate(BaseModel):
    id: str = Field(description="The plan item ID to update.")
    status: str = Field(description="New status: in_progress | done | failed")


class ThinkOutput(BaseModel):
    thinking: str = Field(
        description="A structured <think>-style reasoning block."
    )
    evaluation_previous_goal: str = Field(
        description="One-sentence analysis of your last action."
    )
    memory: str = Field(description="1-3 sentences of specific memory.")
    next_goal: str = Field(
        description="State the next immediate goals and actions."
    )
    initial_plan: Optional[List[PlanItem]] = Field(
        default=None,
        description=(
            "Only set on step 0 to define the execution plan. Leave null on all other steps. "
            'e.g., [{"id": "step-1", "description": "Read source file", "status": "pending"}, ...]'
        )
    )
    plan_updates: List[TodoUpdate] = Field(
        default_factory=list,
        description=(
            "Update plan item statuses. Use on step 1+ to mark items in_progress, done, or failed. "
            'e.g., [{"id": "step-1", "status": "done"}, {"id": "step-2", "status": "in_progress"}]'
        )
    )
    actions: List[ActionInputArgs] = Field(
        description=(
            'The list of actions (tool or skill calls) to execute in sequence. '
            'Each action has a "type" ("tool" or "skill"), a "name", and "args" (JSON string). '
            'e.g., [{"type": "tool", "name": "done_tool", "args": "{\"result\": \"D\"}"}, '
            '{"type": "skill", "name": "hello-world_tool", "args": "{\"name\": \"Alice\"}"}]'
        )
    )

    def __str__(self) -> str:
        return (
            f"Thinking: {self.thinking}\n"
            f"Evaluation of Previous Goal: {self.evaluation_previous_goal}\n"
            f"Memory: {self.memory}\n"
            f"Next Goal: {self.next_goal}\n"
            f"Initial Plan: {self.initial_plan}\n"
            f"Plan Updates: {self.plan_updates}\n"
            f"Actions:\n{format_actions(self.actions)}\n"
        )

    def __repr__(self) -> str:
        return self.__str__()

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

        # Run PRE_MESSAGES middleware pipeline (token count, truncation, history summary)
        hook_ctx = HookContext(
            event=HookEvent.PRE_MESSAGES,
            id=ctx.id,
            agent_name=self.name,
            messages=messages,
            max_tokens=getattr(config, "max_tokens", 0),
        )
        result = await hook_manager(hook_ctx)
        messages = result.modified_messages if result.modified_messages is not None else messages
        additional_context = result.additional_context

        # Inject additional_context (e.g. from history summary) as a system reminder
        if additional_context:
            from src.message import SystemMessage
            messages = list(messages) + [SystemMessage(content=additional_context)]

        return messages

    async def __call__(self,
                       task: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None,
                       **kwargs: Any,
                       ) -> "AgentResponse":
        """Public entry: every direct call routes through the runtime so that
        every agent invocation gets a mailbox-managed lifecycle. Subclasses
        override ``_run``, not ``__call__``."""
        # Local import to break the import cycle between agent and runtime.
        from src.runtime import runtime_manager

        invoke_kwargs: Dict[str, Any] = dict(kwargs)
        if files is not None:
            invoke_kwargs["files"] = files
        if ctx is not None:
            invoke_kwargs["ctx"] = ctx
        return await runtime_manager.invoke(self, task=task, **invoke_kwargs)

    async def _run(self,
                   task: Optional[str] = None,
                   files: Optional[List[str]] = None,
                   ctx: Optional[AgentContext] = None,
                   **kwargs: Any,
                   ) -> "AgentResponse":
        """Actual agent implementation. Subclasses must override."""
        raise NotImplementedError(
            f"{self.__class__.__name__}._run must be implemented by the subclass"
        )


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
    "PlanItem",
    "TodoUpdate",
    "ThinkOutput",
    "Agent",
    "AgentResponse",
]
