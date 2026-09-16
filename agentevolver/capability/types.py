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
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple


class Role(str, Enum):
    """How an agent stands in relation to a component type.

    Three relations, and they are not degrees of the same thing:

    ``CAPABILITY``
        Something the agent *uses*. It appears in a roster — ``<tool-context>``,
        ``<skill-context>``, ``<subagent-context>`` — and the agent picks one and calls
        it. A sub-agent is one of these: dispatching one is a call like any other.

    ``ENVIRONMENT``
        Something the agent *acts in*. There is no roster to pick from; there is a live
        state, refreshed each step, and actions that change it. The prompt says as much —
        every other type is announced as "the X you may call", and this one as
        "environment-state — the live state of the environment you are acting in".

    ``MEMORY``
        Something the agent *has*. It is neither picked nor inhabited: it is read and
        written on the agent's behalf, and reaches the prompt already merged into context.

    Written as a field because the framework kept discovering the distinction and
    patching around it. ``environment`` sat in the capability table and then needed a
    slots override, a template of its own and a state block to get it back *out* of the
    roster it should never have been in.
    """

    CAPABILITY = "capability"
    ENVIRONMENT = "environment"
    MEMORY = "memory"
    #: Not a component at all — stored and versioned beside the agent that owns it.
    PROMPT = "prompt"


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
    #: How an agent stands in relation to this type — see :class:`Role`. It replaced a
    #: ``mounted: bool``, which could say that ``memory`` was not in a roster but had no
    #: way to say that ``environment`` was in one it did not belong in.
    role: Role = Role.CAPABILITY
    #: Whether one member of this type is a directory rather than a single file.
    #: A skill is a directory with a manifest; a tool is one ``.py``.
    directory: bool = False
    #: Whether a member is a Python class, loaded through ``dynamic_manager``. A skill and
    #: a connector are documents; a tool and a plugin are code.
    class_based: bool = False
    #: For a directory type, the manifest file the loader looks for inside it.
    manifest: str = ""
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


def _prompt_manager() -> Any:
    from agentevolver.prompt.server import prompt_manager
    return prompt_manager


#: Everything an agent is given, in the order an agent node's mount pickers appear on the
#: canvas — so the order is part of the UI and not arbitrary, and moving a row rearranges
#: a panel people already know. ``environment`` sits fifth for that reason, even though it
#: is the one row here that is not a capability.
MOUNTED_TYPES: Tuple[CapabilityType, ...] = (
    CapabilityType("tool", _tool_manager, container=False, judgeable=True, mount_type="tools",
                   class_based=True),
    CapabilityType("skill", _skill_manager, container=False, judgeable=True, mount_type="skills",
                   directory=True, manifest="SKILL.md"),
    CapabilityType("connector", _connector_manager, container=True, judgeable=True,
                   mount_type="connectors", directory=True, manifest="CONNECTOR.md"),
    CapabilityType("agent", _agent_manager, container=False, judgeable=False, mount_type="agents",
                   class_based=True),
    CapabilityType("environment", _environment_manager, container=True, judgeable=True,
                   mount_type="environments", directory=True, class_based=True,
                   entry="environment.py", manifest="ENVIRONMENT.md",
                   role=Role.ENVIRONMENT),
    CapabilityType("workflow", _workflow_manager, container=False, judgeable=False,
                   mount_type="workflows", suffix=".html"),
    # Appended rather than grouped with the other containers: the order is the
    # order of an agent node's mount pickers, and moving an existing one would
    # rearrange a panel people already know.
    CapabilityType("plugin", _plugin_manager, container=True, judgeable=True, mount_type="plugins",
                   directory=True, class_based=True, entry="plugin.py", manifest="PLUGIN.md"),
)

#: The six an agent *uses*: it reads a roster, picks a name and calls it. Exactly the six
#: blocks ``capability_context.html`` renders, which is what makes the name honest — this
#: constant held seven for as long as ``environment`` was in it, and every reader of the
#: word "capability" had to know that one of them was not one.
CAPABILITY_TYPES: Tuple[CapabilityType, ...] = tuple(
    entry for entry in MOUNTED_TYPES if entry.role is Role.CAPABILITY)

def _only(role: Role) -> CapabilityType:
    """The single row carrying ``role``, or a failure that says which one is missing.

    A bare ``next()`` raises ``StopIteration`` at import time — the whole package fails to
    load, and the message names neither the role nor this file.
    """
    found = [entry for entry in MOUNTED_TYPES if entry.role is role]
    if len(found) != 1:
        raise ValueError(
            f"expected exactly one {role.value} row in MOUNTED_TYPES, found "
            f"{[entry.type for entry in found]}"
        )
    return found[0]


#: The environment an agent acts in. Resolved by role rather than by name so that the row
#: and the role cannot disagree — but there is exactly one, and three places need *this*
#: type rather than "whatever is not a capability".
ENVIRONMENT_TYPE: CapabilityType = _only(Role.ENVIRONMENT)

#: What an agent *has*: read and written on its behalf, never picked from a roster, and so
#: never offered to a model as something to call.
MEMORY_TYPE: CapabilityType = CapabilityType(
    "memory", _memory_manager, container=False, judgeable=True, mount_type="memories",
    class_based=True, role=Role.MEMORY,
)

#: Every component the framework registers, versions and can evolve: the six capabilities,
#: the environment and memory. This is what the generate / optimize / evaluate agents work
#: on, and what registration, promotion and the slash commands walk — none of which cares
#: how a model reaches the thing, only what it is made of and where it goes.
COMPONENT_TYPES: Tuple[CapabilityType, ...] = MOUNTED_TYPES + (MEMORY_TYPE,)

#: ``prompt`` is not a component: it is not generated, versioned or evolved on its own —
#: an agent's registration hook takes a prompt-only change as part of that agent. But the
#: extension tree stores one, promotion copies one, and the commands address one, so its
#: shape has to be written down somewhere. Written here, next to the others, because the
#: alternative was each of those three places appending its own line about prompts:
#: ``extension/server.py`` and ``sandbox/project.py`` each did, and a fourth would have
#: been added the next time something walked the tree.
PROMPT: CapabilityType = CapabilityType(
    "prompt", _prompt_manager, container=False, judgeable=True, mount_type="prompts",
    suffix=".html", role=Role.PROMPT,
)

#: Everything the extension tree stores — the eight components plus ``prompt``. What a
#: walker of the tree iterates; :data:`COMPONENT_TYPES` is what an evolution agent works on.
STORED_TYPES: Tuple[CapabilityType, ...] = COMPONENT_TYPES + (PROMPT,)

_BY_TYPE: Dict[str, CapabilityType] = {entry.type: entry for entry in COMPONENT_TYPES}
_BY_STORED_TYPE: Dict[str, CapabilityType] = {entry.type: entry for entry in STORED_TYPES}
_BY_MOUNTED_TYPE: Dict[str, CapabilityType] = {entry.type: entry for entry in MOUNTED_TYPES}


def component_type(name: str) -> Optional[CapabilityType]:
    """The entry for one component type name, or ``None`` if there is no such type.

    ``None`` is the answer for ``prompt`` and anything else the framework stores
    but does not evolve on its own, so a caller can ask about any string it was
    given without guarding first.
    """
    return _BY_TYPE.get(name)


def stored_type(name: str) -> Optional[CapabilityType]:
    """The entry for anything the extension tree stores, ``prompt`` included.

    Wider than :func:`component_type` by exactly that one row. A caller walking the tree
    wants this; a caller asking what an evolution agent may build wants the other, and
    the difference is why they are two functions rather than one with a flag.
    """
    return _BY_STORED_TYPE.get(name)


def mounted_type(name: str) -> Optional[CapabilityType]:
    """The entry for anything an agent is given — the six capabilities and the environment.

    What dispatch, prompt assembly and the plan gate ask, because all three act on a type
    the agent has been handed and none of them cares whether the agent calls it or acts in
    it. Asking :func:`capability_type` instead answers ``None`` for ``environment``, which
    reads as "not registered" — the plan gate would stop judging environment actions and
    the render path would raise on the missing entry.
    """
    return _BY_MOUNTED_TYPE.get(name)


def capability_type(name: str) -> Optional[CapabilityType]:
    """The entry for one *callable* type name, or ``None``.

    Narrower than :func:`component_type` by exactly the rows a model cannot call:
    ``memory`` answers ``None`` here and an entry there.
    """
    entry = _BY_TYPE.get(name)
    return entry if entry is not None and entry.role is Role.CAPABILITY else None


#: Type names, in table order. What a caller shows a person choosing one.
CAPABILITY_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in CAPABILITY_TYPES)

#: Names of everything an agent is given, capability or environment.
MOUNTED_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in MOUNTED_TYPES)

#: Every component type's name, in table order.
COMPONENT_TYPE_NAMES: Tuple[str, ...] = tuple(entry.type for entry in COMPONENT_TYPES)

#: Mount names, in table order — the rosters an agent node can be given. From
#: :data:`MOUNTED_TYPES`, not :data:`CAPABILITY_TYPES`: a person mounts an environment on
#: an agent exactly as they mount a tool, even though the agent then acts in it rather
#: than calling it.
AGENT_MOUNT_TYPES: Tuple[str, ...] = tuple(entry.mount_type for entry in MOUNTED_TYPES)


__all__ = [
    "AGENT_MOUNT_TYPES", "CAPABILITY_TYPES", "CAPABILITY_TYPE_NAMES",
    "COMPONENT_TYPES", "COMPONENT_TYPE_NAMES", "ENVIRONMENT_TYPE", "MEMORY_TYPE",
    "MOUNTED_TYPES", "MOUNTED_TYPE_NAMES", "PROMPT", "STORED_TYPES",
    "CapabilityType", "Role", "capability_type", "component_type", "mounted_type",
    "stored_type",
]
