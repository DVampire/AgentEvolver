"""Agent Server

Server implementation for the Agent Context Protocol with lazy loading support.
"""

from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.agent.context import AgentContextManager
from agentevolver.agent.types import Agent, AgentConfig, AgentContext
from agentevolver.capability import CapabilitySchema, SchemaSource, roster, roster_card
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.utils import assemble_workspace_path

# Delegation is a control message, not a document transport.  Large requirements belong
# in an attached artifact so they are inspectable, reusable, and do not turn one tool call
# into an unbounded generation.  This limit is enforced both in the JSON schema and again
# at dispatch time (provider-side schema enforcement is not universal).
MAX_DELEGATED_TASK_CHARS = 12_000
MAX_DELEGATION_FILES = 16
MAX_DELEGATION_CONTRACT_ITEMS = 32
MAX_DELEGATION_CONTRACT_ITEM_CHARS = 1_000


def validate_dispatch_input(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate the bounded, structured agent-to-agent handoff contract."""
    value = dict(raw or {})
    task = value.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("sub-agent delegation requires a non-empty task")
    if len(task) > MAX_DELEGATED_TASK_CHARS:
        raise ValueError(
            f"sub-agent task is {len(task)} characters; maximum is "
            f"{MAX_DELEGATED_TASK_CHARS}. Write the detailed specification to a "
            "workspace file, pass it through files, and delegate a concise instruction."
        )

    for name, maximum in (
        ("files", MAX_DELEGATION_FILES),
        ("read_set", MAX_DELEGATION_CONTRACT_ITEMS),
        ("write_set", MAX_DELEGATION_CONTRACT_ITEMS),
        ("acceptance", MAX_DELEGATION_CONTRACT_ITEMS),
    ):
        items = value.get(name)
        if items is None:
            continue
        if not isinstance(items, list):
            raise ValueError(f"sub-agent {name} must be an array")
        if len(items) > maximum:
            raise ValueError(
                f"sub-agent {name} has {len(items)} items; maximum is {maximum}"
            )
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"sub-agent {name} entries must be non-empty strings")
            if len(item) > MAX_DELEGATION_CONTRACT_ITEM_CHARS:
                raise ValueError(
                    f"sub-agent {name} entry is {len(item)} characters; maximum is "
                    f"{MAX_DELEGATION_CONTRACT_ITEM_CHARS}"
                )
    return value

class AgentManagerServer(BaseModel):
    """Agent Manager Server for managing agent registration and execution with lazy loading."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    base_dir: str = Field(default=None, description="The base directory to use for the agents")
    
    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        """Initialize the Agent Server."""
        super().__init__(**kwargs)
        # Context manager is created lazily (config may not be loaded at import time).
        # initialize() reconfigures it with proper base_dir .
        self.agent_context_manager: Optional[AgentContextManager] = None

        
    def _ensure_context_manager(self) -> AgentContextManager:
        """Lazily create the context manager so methods work before initialize() is called."""
        if self.agent_context_manager is None:
            self.agent_context_manager = AgentContextManager()
        return self.agent_context_manager

    async def initialize(self, agent_names: Optional[List[str]] = None):
        """Initialize agents by names using agent context manager with concurrent support.
        
        Args:
            agent_names: List of agent names to initialize. If None, initialize all registered agents.
        """
        
        self.base_dir = assemble_workspace_path(path_manager.under(config.log_root, P.LOG_MODULE, module="agent"))
        logger.info(f"| 📁 agent manager Server base directory: {self.base_dir}")
        
        # Initialize agent context manager
        self.agent_context_manager = AgentContextManager(
            base_dir=self.base_dir,
            model_name="openrouter/gemini-3-flash-preview",
        )
        await self._ensure_context_manager().initialize(agent_names=agent_names)
        
        logger.info("| ✅ Agents initialization completed")

    async def register(self, 
                       agent_cls: Type[Agent],
                       agent_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None) -> AgentConfig:
        """Register an agent class asynchronously.
        
        Args:
            agent_cls: Agent class to register
            agent_config_dict: Configuration dict for agent initialization
            override: Whether to override existing registration
            version: Optional version string
            
        Returns:
            AgentConfig: Agent configuration
        """
        agent_config = await self._ensure_context_manager().register(
            agent_cls, 
            agent_config_dict=agent_config_dict, 
            override=override,
            version=version
        )
        return agent_config
    
    async def get_info(self, agent_name: str) -> Optional[AgentConfig]:
        """Get agent configuration by name
        
        Args:
            agent_name: Agent name
            
        Returns:
            AgentConfig: Agent configuration or None if not found
        """
        return await self._ensure_context_manager().get_info(agent_name)
    
    async def list(self) -> List[str]:
        """List all registered agents

        Returns:
            List[str]: List of agent names
        """
        return await self._ensure_context_manager().list()

    async def get_instruction(self, allowlist: Optional[List[str]] = None,
                              types: Optional[List[str]] = None,
                              level: str = "brief", *,
                              exclude: Optional[str] = None) -> str:
        """The sub-agent roster, as every other capability type renders one.

        The only type that had no ``get_instruction``, which is why sub-agents never
        reached the capability block of a prompt: the generic builder asks the manager
        named by ``MOUNTED_TYPES``, and this one could not answer. The prompts told
        the model to "dispatch a sub-agent from Available Sub-Agents" while nothing
        produced that list, so the roster had to be inferred from the ``*_agent`` entries
        in the tool schemas.

        Args:
            allowlist: Which agents to include. ``None`` is all, ``[]`` is none.
            types: Unused here; accepted so every manager answers the same call.
            level: ``brief`` for the resident roster, ``full`` for one agent's detail.
            exclude: A name to leave out — the caller, so an orchestrator is not
                offered itself.

        Returns:
            The rendered cards, joined.
        """
        names = allowlist if allowlist is not None else await self.list()
        parts: List[str] = []
        for name in names:
            if name == exclude:
                continue
            info = await self.get_info(name)
            if info is None:
                continue
            parts.append(roster_card(
                info.name, getattr(info, "description", "") or "",
                meta=f"v{getattr(info, 'version', '') or '1.0.0'}",
                level=level,
            ))
        return roster(parts)

    async def function_callings(
        self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None,
        *, exclude: Optional[str] = None
    ) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        """Native tool-calling schemas for dispatchable sub-agents, each paired with its
        route. Used by orchestrators (MetaAgent) that project agents as callables. The
        function name is the agent's own registered name (already ``*_agent``, no
        prefixing); the schema is the uniform sub-agent brief.

        ``exclude`` drops one name (the caller, so an orchestrator never dispatches
        itself). Returns ``[(function_calling, ("agent", name)), ...]``.
        """
        names = allowlist if allowlist is not None else await self.list()
        out: List[Tuple[Dict[str, Any], Tuple[Any, ...]]] = []
        for n in names:
            if n == exclude:
                continue
            fc = await self.get_schema(n, format="json")
            if fc:
                out.append((fc, ("agent", n)))
        return out

    @staticmethod
    def _dispatch_parameters() -> Dict[str, Any]:
        """JSON schema for the uniform sub-agent delegation contract.

        Defines the parameters an orchestrator passes when dispatching any sub-agent as
        a callable (``task`` plus optional files, evolution target, and per-capability
        allowlists), so every agent is projected with the same strict function schema.

        Backgrounding is a parameter here rather than a tool of its own. Every registered
        agent is already projected as a callable, so a second, tool-shaped way to reach the
        same children would give the model two names for one act and let the two schemas
        drift.
        """
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_DELEGATED_TASK_CHARS,
                    "description": (
                        "Concise instruction. Put detailed requirements in a workspace "
                        "specification file and pass it through files; do not restate the "
                        "document here. Unless fork=true, the task, attached files, and "
                        "resource/acceptance contract are everything the child receives."
                    ),
                },
                "files": {
                    "type": "array", "maxItems": MAX_DELEGATION_FILES,
                    "items": {"type": "string", "minLength": 1,
                              "maxLength": MAX_DELEGATION_CONTRACT_ITEM_CHARS},
                    "description": "Existing specification/input paths to pass as context; omit if none.",
                },
                "read_set": {
                    "type": "array", "maxItems": MAX_DELEGATION_CONTRACT_ITEMS,
                    "items": {"type": "string", "minLength": 1,
                              "maxLength": MAX_DELEGATION_CONTRACT_ITEM_CHARS},
                    "description": "Workspace paths this subtask may read. Declare this for safe parallel scheduling.",
                },
                "write_set": {
                    "type": "array", "maxItems": MAX_DELEGATION_CONTRACT_ITEMS,
                    "items": {"type": "string", "minLength": 1,
                              "maxLength": MAX_DELEGATION_CONTRACT_ITEM_CHARS},
                    "description": "Workspace paths this subtask may modify. Parent/child paths conflict.",
                },
                "acceptance": {
                    "type": "array", "maxItems": MAX_DELEGATION_CONTRACT_ITEMS,
                    "items": {"type": "string", "minLength": 1,
                              "maxLength": MAX_DELEGATION_CONTRACT_ITEM_CHARS},
                    "description": "Concrete, independently checkable conditions the child must satisfy.",
                },
                "isolate_worktree": {"type": "boolean", "description": "Run this child in a disposable Git worktree and return its patch. Required for parallel writing children."},
                "owner": {"type": "string", "description": "Task-graph owner responsible for this subtask; defaults to the dispatching agent."},
                "model": {"type": "string", "description": "Registered model route to use for this child invocation only."},
                "reasoning_effort": {"type": "string", "enum": ["none", "minimal", "low", "medium", "high", "xhigh"], "description": "Provider reasoning effort for this child invocation only."},
                "token_budget": {"type": "integer", "minimum": 1, "description": "Hard cumulative LLM-token budget for this child invocation only."},
                "run_in_background": {"type": "boolean", "description": "Outlive this round: return a job id now and keep working while you do other things. Collect with job__output, stop with job__kill. You do NOT need this to parallelise — dispatch calls in one turn already run together. Use it when the work is longer than you are willing to wait for, or when you want to act on something else before it finishes."},
                "continuable": {"type": "boolean", "description": "Background only: keep it alive between turns so send_message_tool can give it more work on the same conversation. Default false — it answers once and ends."},
                "subscription_topics": {"type": "array", "items": {"type": "string"}, "description": "Background subscriber only: logical topics whose published events become serialized turns. Requires run_in_background=true and continuable=true. Registration is idle; the standing task brief runs only when an event arrives."},
                "isolate_workspace": {"type": "boolean", "description": "Give this child a private scratch workspace under the current session. Use for concurrent user/reviewer agents that must not see or overwrite sibling plans or artifacts."},
                "fork": {"type": "boolean", "description": "Let it read your conversation so far as context, instead of starting from only this task text. Use it when what you have already found is what makes the task make sense — files you ruled out, an approach that failed, a decision and why. Default false: a fresh worker on a self-contained job does not need your history and reads faster without it."},
                "target_name": {"type": "string", "description": "ONLY for evaluator/optimizer/generator: the capability being evaluated/improved/created."},
                "target_type": {"type": "string", "enum": ["tool", "skill", "agent", "connector", "memory", "plugin", "workflow", "environment"], "description": "ONLY for capability_generate/optimize/evaluate_agent: which kind of component to create, improve or judge. A generate run's target does not exist yet, so this cannot be looked up — unstated, the run cannot install what it built."},
                "tool_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Evolution probe only: restrict the sub-agent to exactly these tools (empty list = baseline with none)."},
                "skill_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Evolution probe only: restrict the sub-agent to exactly these skills."},
                "connector_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Evolution probe only: restrict the sub-agent to exactly these connectors."},
                "plugin_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Restrict the sub-agent to exactly these plugins (empty list = none)."},
                "environment_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Restrict the sub-agent to these Environment capabilities."},
                "workflow_allowlist": {"type": "array", "items": {"type": "string"}, "description": "Evolution probe only: restrict the sub-agent to exactly these workflows."},
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """Return the strict, uniform sub-agent delegation contract."""
        info = await self.get_info(name)
        if info is None:
            return None
        return CapabilitySchema(
            name=name, description=getattr(info, "description", "") or name,
            parameters=self._dispatch_parameters(), strict=True,
            source=SchemaSource.DECLARED,
        ).render(format)
    
    
    async def get(self, agent_name: str) -> Optional[Agent]:
        """Get agent instance by name
        
        Args:
            agent_name: Agent name
            
        Returns:
            Agent: Agent instance or None if not found
        """
        agent = await self._ensure_context_manager().get(agent_name)
        return agent
    
    async def cleanup(self):
        """Cleanup all agents"""
        await self._ensure_context_manager().cleanup()
    
    async def update(self, 
                     agent_cls: Type[Agent],
                     agent_config_dict: Optional[Dict[str, Any]] = None,
                     new_version: Optional[str] = None, 
                     description: Optional[str] = None) -> AgentConfig:
        """Update an existing agent with new configuration and create a new version
        
        Args:
            agent_cls: New agent class with updated implementation
            agent_config_dict: Configuration dict for agent initialization
            new_version: New version string. If None, auto-increments from current version.
            description: Description for this version update
            
        Returns:
            AgentConfig: Updated agent configuration
        """
        agent_config = await self._ensure_context_manager().update(
            agent_cls, agent_config_dict=agent_config_dict, new_version=new_version, description=description
        )
        return agent_config
    
    async def copy(self, 
                  agent_name: str,
                  new_name: Optional[str] = None, 
                  new_version: Optional[str] = None, 
                  new_config: Optional[Dict[str, Any]] = None) -> AgentConfig:
        """Copy an existing agent
        
        Args:
            agent_name: Name of the agent to copy
            new_name: New name for the copied agent. If None, uses original name.
            new_version: New version for the copied agent. If None, increments version.
            new_config: New configuration dict for the copied agent. If None, uses original config.
            
        Returns:
            AgentConfig: New agent configuration
        """
        agent_config = await self._ensure_context_manager().copy(
            agent_name, new_name, new_version, new_config
        )
        return agent_config
    
    async def unregister(self, agent_name: str) -> bool:
        """Unregister an agent
        
        Args:
            agent_name: Name of the agent to unregister
            
        Returns:
            True if unregistered successfully, False otherwise
        """
        return await self._ensure_context_manager().unregister(agent_name)
    
    async def restore(self, agent_name: str, version: str, auto_initialize: bool = True) -> Optional[AgentConfig]:
        """Restore a specific version of an agent from history
        
        Args:
            agent_name: Name of the agent
            version: Version string to restore
            auto_initialize: Whether to automatically initialize the restored agent
            
        Returns:
            AgentConfig of the restored version, or None if not found
        """
        return await self._ensure_context_manager().restore(agent_name, version, auto_initialize)
    
    async def __call__(self, 
                       name: str, 
                       input: Dict[str, Any], 
                       ctx: AgentContext = None,
                       **kwargs) -> Any:
        """Call an agent method using context manager.
        
        Args:
            name: Name of the agent
            input: Input for the agent
            ctx: Agent context
            **kwargs: Keyword arguments for the agent
            
        Returns:
            Agent result
        """
        
        # Ensure ctx is always an AgentContext instance
        ctx = AgentContext.from_context(ctx) if ctx else AgentContext(name=name, input=input)
        # Direct Python entry points historically supplied only an id. Attach those
        # contexts to a session sandbox just as Gateway and CLI invocations are.
        from agentevolver.session.context import ensure_session_sandbox, stage_input_files
        # No explicit root: the layout puts this exactly where the gateway puts
        # a session, so a locally-started task and a browser-started one share
        # output/<owner>/sessions/<id> rather than diverging.
        ensure_session_sandbox(
            ctx,
            shared_extension_root=config.extension_root,
        )
        input = stage_input_files(ctx, input)

        return await self._ensure_context_manager()(name, input, ctx=ctx, **kwargs)


# Global Agent manager instance
agent_manager = AgentManagerServer()
