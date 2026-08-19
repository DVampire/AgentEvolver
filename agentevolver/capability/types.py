"""The capability types, and what the framework knows about each one.

A **capability** is something a model can call. Seven types are projected into the
native tool list — tool, skill, connector, agent, environment, workflow, plugin —
and each is described here once, in the one place that knows.

A **component** is anything the framework registers, versions and can evolve: those
seven plus ``memory``, which an agent has rather than calls. :data:`COMPONENT_TYPES`
is that wider set, and it is what the generate / optimize / evaluate agents work on.
Everything that builds a roster for a model walks :data:`CAPABILITY_TYPES` instead,
so a model is never offered something it cannot call.

Written down because the same facts were being restated wherever they were
needed: which manager owns a type, whether a type's members are separately
callable, whether the plan gate can rule on it, what an agent mounts it as. Each
restatement was a place the next type would have to be remembered, and a place
two answers could disagree.

Its **artifact shape** — directory or file, which entry file, which extension — is here
too, for the same reason. Three places knew it independently: the registration hook that
installs a generated component, the sandbox that promotes one out of staging, and the skill
that tells a run where to write. They disagreed by omission rather than by argument, and
the omissions were invisible: the sandbox's list of modules stopped at six, so ``workflow``,
``plugin`` and ``memory`` could be generated and registered but never *promoted* — a run
would build one, and promotion would answer "Requested staged extension component was not
found" for a file sitting right there.

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
    #: Whether a model can call this type at all. False for a component the
    #: framework registers, versions and evolves but never hands to a model —
    #: ``memory``, which an agent has rather than calls. Such a row is in
    #: :data:`COMPONENT_TYPES` and deliberately not in :data:`CAPABILITY_TYPES`,
    #: because everything that walks the latter is building a callable roster.
    mounted: bool = True
    #: Whether one member of this type is a directory rather than a single file.
    #: A skill is a directory with a manifest; a tool is one ``.py``.
    directory: bool = False
    #: For a directory holding a Python class, the file its loader reads.
    entry: str = ""
    #: The file extension, for the single-file case.
    suffix: str = ".py"


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


def _memory_manager() -> Any:
    from agentevolver.memory import memory_manager
    return memory_manager


#: Every model-callable capability type. The order is the order an agent's mount
#: pickers appear on the canvas, so it is part of the UI and not arbitrary.
CAPABILITY_TYPES: Tuple[CapabilityType, ...] = (
    CapabilityType("tool", _tool_manager, container=False, judgeable=True, mount_type="tools"),
    CapabilityType("skill", _skill_manager, container=False, judgeable=True, mount_type="skills",
                   directory=True),
    CapabilityType("connector", _connector_manager, container=True, judgeable=True,
                   mount_type="connectors", directory=True),
    CapabilityType("agent", _agent_manager, container=False, judgeable=False, mount_type="agents"),
    CapabilityType("environment", _environment_manager, container=True, judgeable=True,
                   mount_type="environments", directory=True, entry="environment.py"),
    CapabilityType("workflow", _workflow_manager, container=False, judgeable=False,
                   mount_type="workflows", suffix=".html"),
    # Appended rather than grouped with the other containers: the order is the
    # order of an agent node's mount pickers, and moving an existing one would
    # rearrange a panel people already know.
    CapabilityType("plugin", _plugin_manager, container=True, judgeable=True, mount_type="plugins",
                   directory=True, entry="plugin.py"),
)

#: Every component the framework registers, versions and can evolve — the seven
#: callable ones plus ``memory``. This is what a tool that reads registry facts
#: iterates, and what the generate / optimize / evaluate agents work on. It is a
#: superset rather than a replacement: a roster builder wants only the callable
#: rows, and reading them off this table would offer a model something it cannot
#: call.
COMPONENT_TYPES: Tuple[CapabilityType, ...] = CAPABILITY_TYPES + (
    CapabilityType("memory", _memory_manager, container=False, judgeable=True,
                   mount_type="memories", mounted=False),
)

_BY_TYPE: Dict[str, CapabilityType] = {entry.type: entry for entry in COMPONENT_TYPES}


def component_type(name: str) -> Optional[CapabilityType]:
    """The entry for one component type name, or ``None`` if there is no such type.

    ``None`` is the answer for ``prompt`` and anything else the framework stores
    but does not evolve on its own, so a caller can ask about any string it was
    given without guarding first.
    """
    return _BY_TYPE.get(name)


def capability_type(name: str) -> Optional[CapabilityType]:
    """The entry for one *callable* type name, or ``None``.

    Narrower than :func:`component_type` by exactly the rows a model cannot call:
    ``memory`` answers ``None`` here and an entry there.
    """
    entry = _BY_TYPE.get(name)
    return entry if entry is not None and entry.mounted else None


#: Type names, in table order. What a caller shows a person choosing one.
CAPABILITY_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in CAPABILITY_TYPES)

#: Every component type's name, in table order.
COMPONENT_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in COMPONENT_TYPES)

#: Mount names, in table order — the rosters an agent node can be given.
AGENT_MOUNT_TYPES: Tuple[str, ...] = tuple(entry.mount_type for entry in CAPABILITY_TYPES)


__all__ = [
    "AGENT_MOUNT_TYPES", "CAPABILITY_TYPES", "CAPABILITY_TYPE_NAMES",
    "CapabilityType", "capability_type",
]
