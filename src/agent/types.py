"""Agent Context Protocol (agent manager) Types

Core type definitions for the Agent Context Protocol and common Agent
abstractions, aligned with the design of `src.tool.types`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Type


from pydantic import BaseModel, ConfigDict, Field

from src.dynamic import dynamic_manager
from src.logger import logger
from src.memory import memory_manager
from src.message import Message
from src.prompt import prompt_manager
from src.tool import tool_manager
from src.skill import skill_manager
from src.constraint import (
    constraint_manager,
    render_status_text,
    StepConstraint,
    TokenConstraint,
    WallTimeConstraint,
)
from src.session import BaseContext
from src.constraint import Constraint
from src.registry import CONSTRAINT
from src.response import Response
from src.utils import (
    assemble_project_path,
    get_project_root,
)

class AgentContext(BaseContext):
    """Context passed into agent manager and individual agent instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for this agent invocation.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this agent invocation.")
    work_dir: Optional[str] = Field(default=None, description="Working directory for file and git tools.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the agent.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this agent context.")
    parent_session_id: Optional[str] = Field(default=None, description="Name of the parent MetaAgent, used by trace and escalation hooks.")
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
    type: str = Field(description='The type of this action: "tool", "skill", "env", "finish", or "text".')
    name: str = Field(description='The name of the tool, skill, or environment action; "finish" to end the task; "text" for plain-text responses.')
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
    reasoning: str = Field(
        description=(
            "Structured reasoning in three parts: "
            "past — previous goal, last-step outcome, and progress so far; "
            "present — analysis of the current situation; "
            "future — the immediate next goal and the plan to achieve it."
        ),
    )
    plan: List[AgentPlanStep] = Field(
        min_length=1,
        description=(
            "The next action(s) to execute, in order — the plan is NEVER empty. "
            "Each step is exactly one action. Do not re-emit steps already executed "
            "in earlier turns. When the task is complete, the plan MUST be exactly one "
            "step that calls done_tool with the result — 'nothing left to do' means "
            "'call done_tool now', never an empty plan."
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
        max_step: int = 20,
        max_token: Optional[int] = None,
        timeout: Optional[float] = None,
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
        self.max_step = max_step if max_step > 0 else int(1e8)
        self.max_actions = max_actions

        self.review_steps = review_steps

        # Resource budgets — fed into constraint checks as per-call overrides,
        # so agent-level limits take precedence over constraint defaults.
        self.max_token = max_token
        self.timeout = timeout

        # Runtime constraints — accept Constraint instances or mmengine-style dicts
        # e.g. {"type": "StepConstraint", "max_step": 20}
        # Registration with the constraint manager happens in `initialize` (async).
        self.constraints: List[Constraint] = self._build_constraints(constraints)

        # Auto-attach constraints for explicitly requested budgets
        # StepConstraint only DISPLAYS the step budget: the loop (`while step < max_step`)
        # stops at the same value first, so it never blocks early — no off-by-one kill.
        if max_step and max_step > 0 and not any(isinstance(c, StepConstraint) for c in self.constraints):
            self.constraints.append(StepConstraint(max_step=max_step))
        if max_token is not None and not any(isinstance(c, TokenConstraint) for c in self.constraints):
            self.constraints.append(TokenConstraint(max_token=max_token))
        if timeout is not None and not any(isinstance(c, WallTimeConstraint) for c in self.constraints):
            self.constraints.append(WallTimeConstraint(max_second=timeout))
        # Tokens consumed by the previous step, fed into the next constraint check (keyed by task_id)
        self._pending_step_tokens: Dict[str, int] = {}

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

        # Register runtime constraints with the global constraint manager
        for c in self.constraints:
            await constraint_manager.register(c)

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
        step_info_body = (
            f"Step {step_number + 1} of {self.max_step} max possible steps\n"
            f"Current date and time: {time_str}"
        )

        # Clean per-section bodies (no "### " prefix) — each is rendered as its own
        # agent_context sub-module (see code_agent.html and the agent prompts).
        memory_body = "[Memory is disabled.]"
        if self.use_memory and self.memory_name:
            try:
                memory_info = await memory_manager.get_info(self.memory_name)
                if memory_info and memory_info.instance is not None:
                    session_id = ctx.id if ctx else ""
                    mem_text = await memory_info.instance.get(
                        session_id=session_id,
                        short_term_n=self.review_steps,
                    )
                    memory_body = mem_text if mem_text else "[No memory recorded yet.]"
            except Exception:
                pass

        # Resource budgets collected from the previous step's constraint checks
        constraint_status = kwargs.get("constraint_status") or []
        constraint_text = render_status_text(constraint_status) if constraint_status else "[No active budget.]"

        return {
            "step_info": step_info_body,
            "memory_context": memory_body,
            "constraint_text": constraint_text,
        }

    async def _get_tool_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the tool context."""
        contract = await tool_manager.get_contract()
        available_tools = contract if contract else "[No tools loaded.]"
        tool_context = f"### Available Tools\n{available_tools}"
        return {"tool_context": tool_context, "available_tools": available_tools}

    def _allowed_skill_types(self) -> List[str]:
        """Which skill types this agent may see. Workers see 'worker' skills;
        the MetaAgent overrides this to ['orchestrator']. This is the hard
        guardrail that keeps the two skill audiences separate regardless of which
        skills a run happens to load."""
        return ["worker"]

    async def _get_skill_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the skill context from loaded skills via skill manager."""
        skill_content = await skill_manager.get_context(skill_types=self._allowed_skill_types())
        available_skills = skill_content if skill_content else "[No skills loaded.]"
        skill_context = f"### Available Skills\n{available_skills}"
        return {"skill_context": skill_context, "available_skills": available_skills}

    async def _resolve_work_dir(self, ctx: AgentContext, **kwargs) -> str:
        """Resolve the work_dir surfaced in the prompt's `{{ work_dir }}` slot.

        Prefers ctx.work_dir (injected by MetaAgent for sub-agents) over
        self.base_dir so all agents in a MetaAgent run share the same directory.
        """
        return assemble_project_path(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)

    def _workspace_snapshot(self, ctx: Optional[AgentContext]) -> str:
        """A live listing of the working directory's files, refreshed each step.

        Lets an agent see what's currently in its scratch directory without
        spending a tool call. Opt-in: agents that do file work expose this as a
        `workspace` sub-module from their `_get_agent_context` override.
        """
        work_dir = os.path.abspath(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)
        try:
            entries = sorted(os.listdir(work_dir))
            lines = [
                f"  {name}{'/' if os.path.isdir(os.path.join(work_dir, name)) else ''}"
                for name in entries
            ]
            snapshot = "\n".join(lines) if lines else "  (empty)"
        except Exception:
            snapshot = "  (unavailable)"
        return f"{work_dir}\n{snapshot}"

    def _task_with_input_files(self, task: str, **kwargs) -> str:
        """Append the input files the user attached to the task body.

        Input files are part of the assignment (static), so they live inside the
        `task` module rather than a separate block. Only existing paths are shown.
        """
        files = kwargs.get("files") or []
        existing = [f for f in files if os.path.exists(f)]
        if not existing:
            return task
        listing = "\n".join(f"- {f}" for f in existing)
        return f"{task}\n\n**Input files:**\n{listing}"

    async def _get_messages(self,
                            task: str,
                            ctx: AgentContext,
                            **kwargs) -> List[Message]:
        """Build system+agent messages using prompt templates and context."""

        work_dir = await self._resolve_work_dir(ctx=ctx, **kwargs)
        project_root = get_project_root()
        system_modules = dict(max_actions=self.max_actions, work_dir=work_dir, project_root=project_root)
        agent_message_modules = dict(task=self._task_with_input_files(task, **kwargs))
        
        agent_message_modules.update(await self._get_agent_context(task, ctx=ctx, **kwargs))
        agent_message_modules.update(await self._get_tool_context(ctx=ctx))
        agent_message_modules.update(await self._get_skill_context(ctx=ctx))
        
        response = await prompt_manager(
            name=self.prompt_name,
            input={
                "system_modules": system_modules,
                "agent_modules": agent_message_modules,
            },
        )
        if not response.success:
            raise ValueError(response.message)

        return response.data["messages"]

    async def _handle_env_action(
        self,
        action_name: str,
        action_args: Dict[str, Any],
        ctx: "AgentContext",
    ) -> Any:
        """Execute an `env`-type action. Agents bound to an environment override this.

        Implementations should raise on failure so the error reaches
        `action_errors` and is surfaced to the LLM in the next step.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support env actions"
        )

    # ------------------------------------------------------------------
    # Shared execution loop — all tool-calling agents use this
    # ------------------------------------------------------------------

    async def _constraint_check(self, task_id: str, ctx: "AgentContext"):
        """Run the per-step resource-budget check via the constraint hook, exactly once.

        The agent owns its token accounting: it pops the previous step's tokens
        and passes the per-call budget in; the hook runs the checks, blocks on a
        violation, and returns the budget snapshot.

        Returns ``(violation_reason, status_list)``:
          - ``violation_reason``: a string when a budget is exhausted (the caller
            should stop the task), else ``None``;
          - ``status_list``: the budget snapshot to render into the prompt.

        Stateful (step/token counters increment per call), so every agent calls it
        exactly once per step — at the top of the loop, before ``_get_messages`` —
        and never inside ``_think_and_act``. See the canonical loop in
        ``_think_and_act``'s docstring.
        """
        if not self.constraints:
            return None, []
        from src.hook.server import hook_manager
        from src.hook.types import HookDecision, HookEvent
        result = await hook_manager(
            name="constraint_hook",
            input={
                "event": HookEvent.PRE_STEP,
                "agent_name": self.name,
                "task_id": task_id,
                "constraint_names": [c.name for c in self.constraints],
                "check_input": {
                    "token": self._pending_step_tokens.pop(task_id, 0),
                    "max_step": self.max_step,
                    "max_token": self.max_token,
                    "max_second": self.timeout,
                },
            },
            ctx=ctx,
        )
        if result.decision == HookDecision.BLOCK:
            for c in self.constraints:
                c._cleanup(task_id)
            self._pending_step_tokens.pop(task_id, None)
            return result.reason, []
        return None, result.constraint_status or []

    async def _think_and_act(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        **kwargs,
    ) -> Dict[str, Any]:
        """One step of the think-and-act loop: run the LLM on ``messages`` and
        execute the planned actions. Returns done/result/reasoning/action_errors.

        The per-step resource-budget check is the CALLER's responsibility — every
        agent runs ``_constraint_check`` BEFORE building ``messages`` (so the prompt
        reflects the current budget) and stops on a violation. The check is stateful
        (counts a step), so it must run exactly once per step and is NOT repeated
        here. Canonical loop every agent follows::

            step = 0
            action_errors = []
            while step < self.max_step:
                reason, status = await self._constraint_check(task_id, ctx)
                if reason is not None:
                    response = {"done": True, "result": reason,
                                "stopped_by_constraint": True}
                    break
                messages = await self._get_messages(
                    task, ctx=ctx, step_number=step,
                    action_errors=action_errors, constraint_status=status)
                response = await self._think_and_act(messages, task_id, step, ctx=ctx)
                step += 1
                action_errors = response.get("action_errors") or []
                if response["done"]:
                    break
        """
        from src.hook.server import hook_manager
        from src.hook.types import HookDecision, HookEvent
        from src.model import model_manager
        from src.utils import parse_tool_args

        done = False
        result = None
        reasoning = None
        action_errors = []
        step_tokens = 0
        # The per-step constraint check runs in the caller's loop, BEFORE the
        # message is built (see _constraint_check / the canonical loop above), so
        # it is not repeated here. constraint_status is reported by the caller's
        # check now; kept here only so the result contract stays stable.
        constraint_statuses: List[Dict[str, Any]] = []

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id},
            ctx=ctx,
        )

        step_reasoning = ""
        step_plan: List[Dict[str, Any]] = []

        try:
            llm_response = await model_manager(
                name=self.model_name,
                input={"messages": messages, "response_format": AgentThinkOutput},
                ctx=ctx,
            )
            if llm_response.usage:
                step_tokens += llm_response.usage.total
            think_output = llm_response.parsed_model

            step_reasoning = think_output.reasoning
            plan_steps = think_output.plan
            step_plan = [
                {"description": s.description, "type": s.action.type,
                 "name": s.action.name, "args": s.action.args}
                for s in plan_steps
            ]

            logger.info(f"| 💭 [{self.name}] Reasoning: {step_reasoning}")
            logger.info(f"| 📋 [{self.name}] Plan steps: {len(plan_steps)}")

            # An empty plan wastes a step and, left unaddressed, makes the agent
            # spin until max_step. It usually means the model considers the work
            # done but did not emit the terminal action. Nudge it (via action_errors,
            # surfaced in the next prompt) to finish with done_tool now.
            if not plan_steps:
                action_errors.append(
                    "Your previous plan was EMPTY, which is never valid. If the task is "
                    "already complete, output a plan whose single step calls `done_tool` "
                    "(with the result) NOW — 'nothing left to do' means 'call done_tool'. "
                    "If it is not complete, output the next concrete action."
                )
                logger.warning(f"| ⚠️ [{self.name}] Empty plan — nudging to call done_tool.")

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

                    elif action_type == "env":
                        action_result = await self._handle_env_action(action_name, action_args, ctx)
                        logger.info(f"| ✅ [{self.name}] Env action '{action_name}' completed")

                    elif action_type == "finish":
                        # Loop-native termination signal — consumed by the agent
                        # itself, no manager involved (tool-free agents use this
                        # instead of done_tool).
                        done = True
                        result = action_args.get("result", "")
                        reasoning = action_args.get("reasoning")
                        action_result = result
                        logger.info(f"| 🏁 [{self.name}] Finish: {str(result)[:200]}")

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
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "reasoning": step_reasoning, "use_memory": self.use_memory, "memory_name": self.memory_name},
            ctx=ctx,
        )
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id, "reasoning": step_reasoning},
            ctx=ctx,
        )
        # Persist a per-step HTML snapshot of the rendered messages (see SnapshotHook).
        await hook_manager(
            name="snapshot_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number,
                   "task_id": task_id, "work_dir": getattr(ctx, "work_dir", None),
                   "messages": messages, "reasoning": step_reasoning, "plan": step_plan},
            ctx=ctx,
        )

        # Carry this step's token usage into the next step's constraint check
        self._pending_step_tokens[task_id] = step_tokens

        # Clean up constraint session when task finishes
        if done and self.constraints:
            for c in self.constraints:
                c._cleanup(task_id)
            self._pending_step_tokens.pop(task_id, None)

        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors, "constraint_status": constraint_statuses, "stopped_by_constraint": False}

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
