"""Agent contracts: what an agent *is* to the rest of the framework.

Types only. The execution loop used to live in this file — three thousand seven hundred
lines of it — which meant every module that needed to name an ``AgentContext`` imported
the whole runtime with it. The loop is now
:class:`agentevolver.agent.loop.agent.Agent`, re-exported below so
``from agentevolver.agent.types import Agent`` keeps naming the current base class.

| Symbol | Is |
|---|---|
| `AgentContext` | one invocation's session, input and extras |
| `InputArgs` | the task/files an agent is called with |
| `AgentType` | the execution contract a config may still name |
| `AgentConfig` | a registered agent: class, version, schema, live instance |
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.dynamic import dynamic_manager
from agentevolver.session import BaseContext


class AgentContext(BaseContext):
    """Context passed into agent manager and individual agent instances."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for this agent invocation.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this agent invocation.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the agent.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this agent context.")
    parent_session_id: Optional[str] = Field(default=None, description="Name of the parent MetaAgent, used by trace and escalation hooks.")
    subtask_id: Optional[str] = Field(default=None, description="ID of the subtask record in the parent MetaAgent's plan.")

class InputArgs(BaseModel):
    task: str = Field(description="The task to complete.")
    files: Optional[List[str]] = Field(default=None, description="The files to attach to the task.")

class AgentType(str, Enum):
    """Execution contract used by an agent."""

    TOOL_CALLING = "tool_calling"
    PROCEDURAL = "procedural"

    @classmethod
    def _missing_(cls, value):
        """Map legacy agent-type strings to a valid member (Enum lookup fallback).

        Preserves backward compatibility for configs written under the old informal
        ``"workflow"`` name, now folded into ``PROCEDURAL``. Returns ``None`` for any
        other unknown value so the Enum raises the usual ``ValueError``.
        """
        # Backward compatibility for configs created under the old informal name.
        if value == "workflow":
            return cls.PROCEDURAL
        return None


class AgentConfig(BaseModel):
    """Agent configuration for registration, similar to `ToolConfig`."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the agent")
    description: str = Field(description="The description of the agent")
    version: str = Field(default="1.0.0", description="Version of the agent")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False, description="Whether the agent may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    agent_type: AgentType = Field(default=AgentType.TOOL_CALLING, description="Agent execution contract")

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
            "agent_type": self.agent_type.value,

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
        agent_type = AgentType(data.get("agent_type", AgentType.TOOL_CALLING))

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
                except Exception:
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
            agent_type=agent_type,
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

# ---------------------------------------------------------------------------
# Event-driven run: one unified loop for every agent (leaf actors AND orchestrators)
# ---------------------------------------------------------------------------
# The runtime pump drives every agent the same way: on_start kicks the first turn and
# returns None; each turn (_advance) runs _think then dispatches the batch as background
# tasks that post _ActionDone back to THIS agent's own inbox; on_event collects them and,
# when the round drains, advances to the next turn or concludes. round == turn.
#
# An orchestrator (MetaAgent) is not special: it just has ``agent`` capabilities in its
# roster, so some of its dispatched actions are sub-agents. A sub-agent that blocks
# escalates to its parent via the escalation channel → the parent's inbox → on_event — which
# works precisely because every agent (parent included) runs this same event-driven loop.


# The base class itself. Imported last so this module stays importable by anything the
# loop needs — `agentevolver.agent.loop` reaches into `agent.context`'s submodules
# directly rather than through its package, so nothing here is circular.
from agentevolver.agent.loop.agent import Agent  # noqa: E402

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentContext",
    "AgentType",
    "InputArgs",
]
