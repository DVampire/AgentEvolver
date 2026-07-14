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
from src.connector import connector_manager
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

# Tools that mutate the framework / deliverables. A read_only agent (e.g. an evaluator)
# is refused these at dispatch time — a coarse guard so a "read-only" agent cannot edit
# source, commit, deploy, or roll back evolution. Read/inspect/probe tools (and calling
# the target under test) stay allowed so evaluators still work. Op-level enforcement
# (allow reads, deny writes per call) is future work.
_READ_ONLY_DENIED_TOOLS = {
    "write_file_tool", "edit_file_tool", "git_tool", "deploy_tool", "evolution_tool",
}


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
    enable_evolving: bool = Field(default=False, description="Whether the agent may be evolved (self-optimized)")
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
            "enable_evolving": self.enable_evolving,
            
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
        enable_evolving = data.get("enable_evolving", False)
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
            enable_evolving=enable_evolving,
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
            f"enable_evolving={self.enable_evolving})"
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
    enable_evolving: bool = Field(default=False, description="Whether the agent may be evolved (self-optimized)")
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
        enable_evolving: bool = False,
        use_memory: bool = True,
        constraints: Optional[List[Constraint]] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        # Set default values
        self.name = name or self.name
        self.description = description or self.description
        self.metadata = metadata or self.metadata
        self.enable_evolving = enable_evolving

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

        # Errors from the previous step (shown only when the last step failed) — a
        # universal agent-context sub-module, provided here so subclasses don't each
        # re-derive it. Same for the live workspace snapshot below.
        action_errors = kwargs.get("action_errors") or []
        errors_body = "\n".join(f"- {e}" for e in action_errors) if action_errors else ""

        # Running todo — injected every step (like memory) when the agent uses todo_tool,
        # so its plan/checklist is always visible without spending a `show` action.
        todo_body = ""
        if ctx is not None:
            try:
                todo_info = await tool_manager.get_info("todo_tool")
                if todo_info and todo_info.instance is not None:
                    todo_body = await todo_info.instance.content(ctx.id)
            except Exception:
                todo_body = ""

        return {
            "step_info": step_info_body,
            "memory_context": memory_body,
            "constraint_text": constraint_text,
            "workspace": self._workspace_snapshot(ctx),
            "errors": errors_body,
            "todo": todo_body,
        }

    async def _get_tool_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the tool context.

        Honors an optional per-run allowlist in ``ctx.extra["tool_allowlist"]`` (a list
        of tool names) — used to run a "with-tool" vs "baseline" agent over the same task.
        ``None`` (default) = all loaded tools; an empty list = no tools (the baseline).
        """
        allowlist = ctx.extra.get("tool_allowlist") if (ctx is not None and getattr(ctx, "extra", None)) else None
        content = await tool_manager.get_instruction(allowlist=allowlist)
        available_tools = content if content else "[No tools loaded.]"
        tool_context = f"### Available Tools\n{available_tools}"
        return {"tool_context": tool_context, "available_tools": available_tools}

    def _allowed_skill_types(self) -> List[str]:
        """Which skill types this agent may see. Workers see 'worker' skills;
        the MetaAgent overrides this to ['orchestrator']. This is the hard
        guardrail that keeps the two skill audiences separate regardless of which
        skills a run happens to load."""
        return ["worker"]

    async def _get_skill_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the skill context from loaded skills via skill manager.

        Honors an optional per-run allowlist in ``ctx.extra["skill_allowlist"]`` (a list
        of skill names) — used by skill evaluation to run a "with-skill" vs a "baseline"
        agent over the same task. ``None`` (default) = all skills of the allowed type;
        an empty list = no skills (the baseline). Normal runs never set it, so behavior
        is unchanged.
        """
        allowlist = ctx.extra.get("skill_allowlist") if (ctx is not None and getattr(ctx, "extra", None)) else None
        skill_content = await skill_manager.get_instruction(
            allowlist=allowlist, types=self._allowed_skill_types()
        )
        available_skills = skill_content if skill_content else "[No skills loaded.]"
        skill_context = f"### Available Skills\n{available_skills}"
        return {"skill_context": skill_context, "available_skills": available_skills}

    async def _get_connector_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Get the connector context from loaded connectors (MCP servers) via connector manager.

        Concise by design (name/description/actions + CONNECTOR.md path). The agent
        reads a connector's CONNECTOR.md on demand for per-action argument details.

        Honors an optional per-run allowlist in ``ctx.extra["connector_allowlist"]`` —
        ``None`` (default) = all loaded connectors; an empty list = none (baseline).
        """
        allowlist = ctx.extra.get("connector_allowlist") if (ctx is not None and getattr(ctx, "extra", None)) else None
        connector_content = await connector_manager.get_instruction(allowlist=allowlist)
        available_connectors = connector_content if connector_content else "[No connectors loaded.]"
        connector_context = f"### Available Connectors\n{available_connectors}"
        return {"connector_context": connector_context, "available_connectors": available_connectors}

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
        system_modules = dict(
            max_actions=self.max_actions, work_dir=work_dir, project_root=project_root,
        )
        agent_message_modules = dict(task=self._task_with_input_files(task, **kwargs))

        agent_message_modules.update(await self._get_agent_context(task, ctx=ctx, **kwargs))
        agent_message_modules.update(await self._get_tool_context(ctx=ctx))
        agent_message_modules.update(await self._get_skill_context(ctx=ctx))
        agent_message_modules.update(await self._get_connector_context(ctx=ctx))
        
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
        """One step of the think-and-act loop (native tool use): the model sees the
        agent's capabilities as native tools, emits tool_calls, and this dispatches
        each back to its owning manager. Returns done/result/reasoning/action_errors.

        Reasoning is the model's thinking/text; completion is an explicit ``done``
        tool call (never inferred from plain text — a text-only turn is nudged to
        act or call done). Tool args arrive as structured objects validated by each
        tool's schema (no JSON-string double-encoding).

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
        from src.hook.types import HookEvent

        # THINK: one LLM turn → a batch of tool_calls (+ routing). Pure decision.
        decision = await self._think(messages, task_id, step_number, ctx)
        step_reasoning = decision["reasoning"]
        step_tokens = decision["step_tokens"]

        # DISPATCH: run this turn's batch concurrently, each call routed to its manager.
        outcome = await self._dispatch(decision, task_id, step_number, ctx)
        done = outcome["done"]
        result = outcome["result"]
        reasoning = outcome["reasoning"]
        action_errors = outcome["action_errors"]
        step_plan = outcome["plan"]

        # --- per-step lifecycle: POST_STEP + snapshot + trajectory capture ---
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
        # Trajectory capture: effective prompt + structured decision + tokens for this step.
        await hook_manager(
            name="trajectory_hook",
            input={"event": HookEvent.POST_STEP, "agent_name": self.name, "step_number": step_number,
                   "task_id": task_id, "messages": messages, "reasoning": step_reasoning,
                   "plan": step_plan, "step_tokens": step_tokens},
            ctx=ctx,
        )

        # Carry this step's token usage into the next step's constraint check.
        self._pending_step_tokens[task_id] = step_tokens
        if done and self.constraints:
            for c in self.constraints:
                c._cleanup(task_id)
            self._pending_step_tokens.pop(task_id, None)

        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors, "constraint_status": [], "stopped_by_constraint": False}

    # ------------------------------------------------------------------
    # The unified loop's two verbs: _think (decide) + _dispatch (act).
    # Shared verbatim by every agent — leaf actors AND the MetaAgent. The only
    # thing an orchestrator adds is a richer roster (include_agents / extra_tools)
    # and a different batch executor; the decision + per-call dispatch are identical.
    # ------------------------------------------------------------------

    async def _think(
        self,
        messages: List[Message],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
        *,
        include_agents: bool = False,
        extra_tools: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """One LLM turn (native tool use): project the agent's capabilities into a flat
        tool list + routing table, stream, and accumulate into a batch of tool_calls.

        Pure decision — no dispatch, no state mutation — so both the leaf-agent loop and
        MetaAgent call this same method. ``include_agents`` / ``extra_tools`` let an
        orchestrator widen the roster (agent__<name>, reply_to_agent). Returns
        ``{tool_calls, routing, reasoning, step_tokens}``.
        """
        from src.hook.server import hook_manager
        from src.hook.types import HookEvent
        from src.model import model_manager
        from src.model.types import accumulate_stream
        from src.agent.native_tools import assemble_native_tools

        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_STEP, "agent_name": self.name, "step_number": step_number, "task_id": task_id},
            ctx=ctx,
        )

        tools, routing = await assemble_native_tools(self, ctx, include_agents=include_agents)
        if extra_tools:
            tools = tools + list(extra_tools)

        reasoning = ""
        tool_calls: List[Any] = []
        step_tokens = 0
        try:
            acc = await accumulate_stream(
                model_manager.stream(
                    name=self.model_name,
                    input={"messages": messages, "tools": tools},
                    ctx=ctx,
                )
            )
            step_tokens = int((acc.get("usage") or {}).get("output_tokens", 0) or 0)
            reasoning = acc.get("thinking") or acc.get("text") or ""
            tool_calls = acc.get("tool_calls") or []
        except Exception as e:
            logger.error(f"| ❌ [{self.name}] Error in _think: {e}")

        logger.info(f"| 💭 [{self.name}] Reasoning: {reasoning[:200]}")
        logger.info(f"| 🔧 [{self.name}] Tool calls: {[c.name for c in tool_calls]}")

        return {"tool_calls": tool_calls, "routing": routing, "reasoning": reasoning, "step_tokens": step_tokens}

    async def _dispatch(
        self,
        decision: Dict[str, Any],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
    ) -> Dict[str, Any]:
        """Run this turn's batch of tool_calls CONCURRENTLY, each routed to its manager.

        A single turn's batch is parallel-safe by the function-calling contract — the
        model only puts independent calls in one batch; dependent work is emitted across
        turns — so we gather the whole batch. Returns
        ``{done, result, reasoning, action_errors, plan}``.
        """
        import json as _json

        tool_calls = decision["tool_calls"]
        routing = decision["routing"]
        reasoning = decision["reasoning"]
        action_errors: List[str] = []
        step_plan = [
            {"id": c.id, "description": "", "type": (routing.get(c.name) or ("tool",))[0],
             "name": c.name, "args": _json.dumps(c.input, ensure_ascii=False)}
            for c in tool_calls
        ]

        # A text-only turn is NOT completion — completion is an explicit `done` tool call,
        # never inferred from plain text. Nudge the model to act or call `done`.
        if not tool_calls:
            action_errors.append(
                "You produced text but called no tool. Take the next action by calling a tool, "
                "or if the task is COMPLETE call `done` with the result now. Do not answer in plain text."
            )
            logger.warning(f"| ⚠️ [{self.name}] No tool call — nudging to act or call done.")
            return {"done": False, "result": None, "reasoning": reasoning, "action_errors": action_errors, "plan": step_plan}

        outcomes = await asyncio.gather(*[
            self._run_one(call, i, routing, task_id, step_number, ctx)
            for i, call in enumerate(tool_calls)
        ])

        done = False
        result = None
        for o in outcomes:
            if o.get("error"):
                action_errors.append(f"Action '{o['name']}' failed: {o['error']}")
            if o.get("done"):
                done = True
                result = o.get("result")
                if o.get("reasoning"):
                    reasoning = o["reasoning"]
        return {"done": done, "result": result, "reasoning": reasoning, "action_errors": action_errors, "plan": step_plan}

    async def _run_one(
        self,
        call: Any,
        index: int,
        routing: Dict[str, Any],
        task_id: str,
        step_number: int,
        ctx: "AgentContext",
    ) -> Dict[str, Any]:
        """Dispatch ONE tool_call, wrapped in its PRE_ACTION → invoke → POST_ACTION hooks.

        This is the atomic unit of action; ``_dispatch`` runs one per tool_call,
        concurrently. Keeping a call's hook pair inside one coroutine means the pairs
        stay correct even when the batch runs in parallel. Returns
        ``{name, done, result, reasoning, error}``.
        """
        import json as _json
        from src.hook.server import hook_manager
        from src.hook.types import HookDecision, HookEvent

        route = routing.get(call.name)
        kind = route[0] if route else "tool"
        args_str = _json.dumps(call.input, ensure_ascii=False)
        logger.info(f"| 📝 [{self.name}] [{kind}] {call.name}: {call.input}")

        action_dict = {
            "index": index, "id": call.id, "description": "", "type": kind,
            "name": call.name, "args": args_str, "args_parsed": call.input,
        }

        done, result, reasoning, error, action_result = False, None, None, None, None

        pre_result = await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.PRE_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "task_id": task_id},
            ctx=ctx,
        )
        if pre_result.decision == HookDecision.BLOCK:
            logger.warning(f"| 🚫 [{self.name}] Action blocked by hook: {pre_result.reason}")
            return {"name": call.name, "done": False, "result": None, "reasoning": None, "error": None}

        try:
            if route is None:
                raise ValueError(f"Unknown tool '{call.name}' (not in the assembled tool set)")
            # read_only agents may not invoke framework-mutating tools.
            if (self.permission_mode == "read_only" and kind == "tool"
                    and route[1] in _READ_ONLY_DENIED_TOOLS):
                raise PermissionError(
                    f"read_only agent '{self.name}' may not invoke framework-mutating "
                    f"tool '{route[1]}'. Report findings instead of modifying anything."
                )
            action_result, done, result, reasoning = await self._invoke_capability(route, call, ctx)
        except Exception as e:
            error = str(e)
            logger.error(f"| ❌ [{self.name}] Action '{call.name}' failed: {e}")

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
        await hook_manager(
            name="trajectory_hook",
            input={"event": HookEvent.POST_ACTION, "agent_name": self.name, "step_number": step_number, "action": action_dict, "action_result": action_result, "task_id": task_id, "error": error},
            ctx=ctx,
        )
        return {"name": call.name, "done": done, "result": result, "reasoning": reasoning, "error": error}

    async def _invoke_capability(self, route: Any, call: Any, ctx: "AgentContext"):
        """Route ONE call to the manager that owns it — the single dispatch table that
        knows how each capability kind executes. Returns
        ``(action_result, done, result, reasoning)``.
        """
        kind = route[0]
        if kind == "skill":
            response = await skill_manager(name=route[1], input=call.input, ctx=ctx)
            logger.info(f"| ✅ [{self.name}] Skill '{route[1]}' completed (success={response.success})")
            return response.message, False, None, None
        if kind == "connector":
            response = await connector_manager(name=route[1], input={"action": route[2], "args": call.input}, ctx=ctx)
            logger.info(f"| ✅ [{self.name}] Connector '{route[1]}' action '{route[2]}' completed (success={response.success})")
            return response.message, False, None, None
        if kind == "env":
            action_result = await self._handle_env_action(route[1], call.input, ctx)
            logger.info(f"| ✅ [{self.name}] Env action '{route[1]}' completed")
            return action_result, False, None, None
        if kind == "tool":
            tool_response = await tool_manager(name=route[1], input=call.input, ctx=ctx)
            logger.info(f"| ✅ [{self.name}] Tool '{route[1]}' completed")
            if route[1] == "done_tool":
                reasoning = (tool_response.data or {}).get("reasoning") if hasattr(tool_response, "data") else None
                return tool_response.message, True, tool_response.message, reasoning
            return tool_response.message, False, None, None
        raise ValueError(f"Unknown route kind {kind!r}")

    # ------------------------------------------------------------------
    # Path 1: Direct call
    # ------------------------------------------------------------------

    async def __call__(self,
                       task: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       ctx: Optional[AgentContext] = None,
                       **kwargs: Any,
                       ) -> "Response":
        """Standard synchronous think-and-act loop shared by all tool-calling agents.

        Runs: ON_START hooks → [constraint check → build messages → think & act] until
        done or max_step → ON_STOP hooks. Extra keyword arguments flow through to
        ``_get_messages`` / ``_think_and_act`` / the context builder.

        Every tool-calling subclass defines its own ``__call__`` as the explicit entry
        point: simple actors (general, code, evaluate) just delegate to
        ``super().__call__(...)``; agents that must register a produced artifact
        (generate, optimize) call ``super().__call__(...)`` and then run their
        registration inline on the returned Response.

        Event-driven / orchestrator agents (MetaAgent, MonitorAgent) override
        ``on_start`` / ``on_event`` instead; an agent with a bespoke loop
        (BrowserAgent) replaces this loop entirely.
        """
        from src.hook.server import hook_manager
        from src.hook.types import HookEvent
        from src.utils.name_utils import make_id
        from src.response import ResponseType

        logger.info(f"| 🚀 Starting {self.name}: {task}")

        if ctx is None:
            ctx = AgentContext()
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir
        if files:
            logger.info(f"| 📂 Attached files: {files}")

        task_id = make_id()
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        # Shared ON_START / ON_STOP hook payload (memory + trace).
        lifecycle_input = {
            "agent_name": self.name,
            "task_id": task_id,
            "memory_name": self.memory_name,
            "use_memory": self.use_memory,
            "parent_session_id": ctx.parent_session_id, 
            "subtask_id": ctx.subtask_id,
        }
        for hook_name in ("memory_hook", "trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={"event": HookEvent.ON_START, "task": task, **lifecycle_input},
                ctx=ctx,
            )

        step_number = 0
        action_errors: List[str] = []
        response = {"done": False, "result": None, "reasoning": None, "action_errors": []}

        while step_number < self.max_step:
            logger.info(f"| 🔄 [{self.name}] Step {step_number + 1}/{self.max_step}")
            # Budget check BEFORE building messages so the prompt reflects the current
            # budget. Runs exactly once per step (_think_and_act does not repeat it).
            reason, constraint_status = await self._constraint_check(task_id, ctx)
            if reason is not None:
                logger.warning(f"| 🛑 {self.name} constraint violated: {reason}")
                response = {"done": True, "result": reason, "reasoning": None,
                            "action_errors": [], "stopped_by_constraint": True}
                break
            messages = await self._get_messages(
                task, ctx=ctx, files=files, step_number=step_number,
                action_errors=action_errors, constraint_status=constraint_status, **kwargs)
            response = await self._think_and_act(
                messages, task_id, step_number, ctx=ctx, **kwargs)
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response["done"]:
                break

        if step_number >= self.max_step and not response["done"]:
            logger.warning(f"| 🛑 [{self.name}] Reached max steps ({self.max_step})")
            response = {"done": False, "result": "The task has not been completed.",
                        "reasoning": "Reached the maximum number of steps."}

        stop_success = response["done"] and not response.get("stopped_by_constraint", False)
        for hook_name in ("memory_hook", "trace_hook", "trajectory_hook"):
            await hook_manager(
                name=hook_name,
                input={"event": HookEvent.ON_STOP, "result": response.get("result"),
                       "success": stop_success, **lifecycle_input},
                ctx=ctx,
            )

        logger.info(f"| ✅ {self.name} completed after {step_number}/{self.max_step} steps")

        # Surface the run's task_id so a caller (e.g. a benchmark/evaluator driver)
        # can correlate this run with its captured trajectory and later call
        # trajectory_manager.set_reward(task_id, reward).
        response["task_id"] = task_id
        return Response(
            type=ResponseType.AGENT,
            success=response["done"] and not response.get("stopped_by_constraint", False),
            message=response.get("result") or "",
            data=response,
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
    "Agent",
    "AgentContext",
]
