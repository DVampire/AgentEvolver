"""The capability types, and what the framework knows about each one.

A **capability** is something a model can call. Seven types are projected into the
native tool list — tool, skill, connector, agent, environment, workflow, plugin —
and each is described here once, in the one place that knows.

Written down because the same facts were being restated wherever they were
needed: which manager owns a type, whether a type's members are separately
callable, whether the plan gate can rule on it, what an agent mounts it as. Each
restatement was a place the next type would have to be remembered, and a place
two answers could disagree.

Two properties do most of the explaining, and neither is "is it a capability":

``container``
    Whether a name addresses one callable thing or a set of them. A ``tool`` is
    one function; a ``connector`` is a server with actions, an ``environment``
    an object with actions, a ``plugin`` a service with tools. The container's
    members are what the model actually calls.

``judgeable``
    Whether the type's effects can be read off a declaration. A tool says
    ``mutates`` and ``permission_mode`` next to the code that knows; an agent or
    a workflow does whatever the thing it runs does, which no declaration can
    state in advance. This is what plan mode gates on.

The manager is held as a callable rather than imported, because every manager
imports things that import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass(frozen=True)
class CapabilityType:
    """One kind of model-callable capability."""

    #: Registry name, and the first element of a dispatch route: ``"tool"``.
    type: str
    #: The owning manager, resolved on demand. Not the manager itself: importing
    #: one here would close a cycle through almost every module in the package.
    manager: Callable[[], Any]
    #: Whether members of this type (actions, tools) are separately callable.
    container: bool
    #: Whether the plan gate can rule on this type from its own declaration.
    judgeable: bool
    #: What an agent mounts a roster of these as, on the canvas. Plural.
    mount_type: str


def _tool_manager() -> Any:
    from agentevolver.tool.server import tool_manager
    return tool_manager


def _skill_manager() -> Any:
    from agentevolver.skill.server import skill_manager
    return skill_manager


def _connector_manager() -> Any:
    from agentevolver.connector.server import connector_manager
    return connector_manager


def _environment_manager() -> Any:
    from agentevolver.environment.server import environment_manager
    return environment_manager


def _agent_manager() -> Any:
    from agentevolver.agent.server import agent_manager
    return agent_manager


def _workflow_manager() -> Any:
    from agentevolver.workflow import workflow_manager
    return workflow_manager


def _plugin_manager() -> Any:
    from agentevolver.plugins import plugin_manager
    return plugin_manager


#: Every model-callable capability type. The order is the order an agent's mount
#: pickers appear on the canvas, so it is part of the UI and not arbitrary.
CAPABILITY_TYPES: Tuple[CapabilityType, ...] = (
    CapabilityType("tool", _tool_manager, container=False, judgeable=True, mount_type="tools"),
    CapabilityType("skill", _skill_manager, container=False, judgeable=True, mount_type="skills"),
    CapabilityType("connector", _connector_manager, container=True, judgeable=True, mount_type="connectors"),
    CapabilityType("agent", _agent_manager, container=False, judgeable=False, mount_type="agents"),
    CapabilityType("environment", _environment_manager, container=True, judgeable=True, mount_type="environments"),
    CapabilityType("workflow", _workflow_manager, container=False, judgeable=False, mount_type="workflows"),
    # Appended rather than grouped with the other containers: the order is the
    # order of an agent node's mount pickers, and moving an existing one would
    # rearrange a panel people already know.
    CapabilityType("plugin", _plugin_manager, container=True, judgeable=True, mount_type="plugins"),
)

_BY_TYPE: Dict[str, CapabilityType] = {entry.type: entry for entry in CAPABILITY_TYPES}


def capability_type(name: str) -> Optional[CapabilityType]:
    """The entry for one type name, or ``None`` if it is not a capability type.

    ``None`` is the answer for ``memory``, ``prompt`` and the rest — things the
    framework registers but never hands a model — so a caller can ask about any
    string it was given without guarding first.
    """
    return _BY_TYPE.get(name)


#: Type names, in table order. What a caller shows a person choosing one.
CAPABILITY_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in CAPABILITY_TYPES)

#: Mount names, in table order — the rosters an agent node can be given.
AGENT_MOUNT_TYPES: Tuple[str, ...] = tuple(entry.mount_type for entry in CAPABILITY_TYPES)


__all__ = [
    "AGENT_MOUNT_TYPES", "CAPABILITY_TYPES", "CAPABILITY_TYPE_NAMES",
    "CapabilityType", "capability_type",
]
