"""Builds the palette and mount rosters.

The canvas is agent-centric (matching AgentEvolver): tools/skills/connectors
are not standalone palette nodes — they are *mounted* on an agent through its
capability picker. The palette therefore contains structural steps, io
declarations, the actor agents, and reusable workflows. Evolver agents
(generator/optimizer/evaluator) stay registered for the self-evolution system
but are hidden from the canvas.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

from agentevolver.canvas.types import NodeSpec, ParamSpec
from agentevolver.logger import logger

# Which capability rosters an agent node can mount (scope). All are optional:
# leaving a picker empty means the agent uses its configured defaults.
AGENT_MOUNT_KINDS = ["tools", "skills", "connectors", "agents", "environments"]


def _is_actor_agent(info: Any) -> bool:
    """True for the user-facing worker agents (agentevolver.agent.actor.*).

    Derived from the class module so the split is self-maintaining: a new actor
    appears automatically, a new evolver agent stays hidden — no name list.
    """
    cls = getattr(info, "cls", None)
    module = getattr(cls, "__module__", "") if cls is not None else ""
    if module:
        return ".agent.actor." in module or module.endswith(".agent.actor")
    return False


def _structural_specs(agent_names: List[str]) -> List[NodeSpec]:
    agent_options = sorted(agent_names) or None
    return [
        NodeSpec(
            id="io/input", category="io", label="Flow input",
            description="Declares one workflow input; reference it anywhere as ${inputs.<name>}.",
            params=[
                ParamSpec(name="name", label="Name", required=True, connectable=False),
                ParamSpec(name="input_type", label="Type", type="select",
                          options=["string", "number", "boolean", "array", "object"], connectable=False),
                ParamSpec(name="required", label="Required", type="boolean", connectable=False),
                ParamSpec(name="default", label="Default", connectable=False),
                ParamSpec(name="description", label="Description", multiline=True, connectable=False),
            ],
        ),
        NodeSpec(
            id="io/output", category="io", label="Flow output",
            description="Publishes one value as a named workflow output.",
            params=[ParamSpec(name="name", label="Name", required=True, connectable=False)],
        ),
        NodeSpec(
            id="step/map", category="structural", step_type="map", label="Map",
            description="Run the contained steps once per item, concurrently.",
            has_items=True, container=True,
            params=[
                ParamSpec(name="item_name", label="Item variable", default="item", connectable=False),
                ParamSpec(name="concurrency", label="Concurrency", type="number", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/branch", category="structural", step_type="branch", label="Branch",
            description="Run the then/else steps depending on a ${...} test expression.",
            container=True,
            params=[ParamSpec(name="condition", label="Test", required=True, multiline=True,
                              description="e.g. ${check} or a comparison over step results", connectable=False)],
        ),
        NodeSpec(
            id="step/loop", category="structural", step_type="loop", label="Loop",
            description="Repeat the contained steps until/while a condition, bounded by max rounds.",
            container=True,
            params=[
                ParamSpec(name="max_rounds", label="Max rounds", type="number", required=True, connectable=False),
                ParamSpec(name="condition", label="Condition", multiline=True, connectable=False),
                ParamSpec(name="condition_mode", label="Mode", type="select",
                          options=["until", "while"], default="until", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/reduce", category="structural", step_type="reduce", label="Reduce",
            description="Fold a list of results into one via an agent.",
            has_task=True, has_items=True,
            params=[ParamSpec(name="target", label="Agent", type="select", required=True,
                              options=agent_options, connectable=False)],
        ),
        NodeSpec(
            id="step/verify", category="structural", step_type="verify", label="Verify",
            description="Independently verify each item via an agent.",
            has_task=True, has_items=True,
            params=[
                ParamSpec(name="target", label="Agent", type="select", required=True,
                          options=agent_options, connectable=False),
                ParamSpec(name="concurrency", label="Concurrency", type="number", connectable=False),
                ParamSpec(name="min_votes", label="Min votes", type="number", connectable=False),
            ],
        ),
        NodeSpec(
            id="step/checkpoint", category="structural", step_type="checkpoint", label="Checkpoint",
            description="Persist run state so the workflow can resume from here.",
        ),
    ]


def _agent_spec(name: str, info: Any) -> NodeSpec:
    return NodeSpec(
        id=f"agent/{name}", category="agent", step_type="agent", target=name,
        label=name.removesuffix("_agent").replace("_", " ").capitalize(),
        description=str(getattr(info, "description", "") or ""),
        has_task=True,
        mount_kinds=list(AGENT_MOUNT_KINDS),
    )


def _workflow_spec(name: str, definition: Any) -> NodeSpec:
    params = [
        ParamSpec(
            name=input_name,
            label=input_name.replace("_", " ").capitalize(),
            type="string" if getattr(spec, "type", "string") == "string" else "json",
            required=bool(getattr(spec, "required", False)),
            default=getattr(spec, "default", None),
            description=str(getattr(spec, "description", "")),
        )
        for input_name, spec in (getattr(definition, "inputs", {}) or {}).items()
    ]
    return NodeSpec(
        id=f"workflow/{name}", category="workflow", step_type="workflow", target=name,
        label=name.replace("_", " ").capitalize(),
        description=str(getattr(definition, "description", "") or ""),
        params=params,
    )


async def build_catalog() -> List[NodeSpec]:
    """Assemble the palette (structural + io + actor agents + workflows)."""
    agent_names: List[str] = []
    try:
        from agentevolver.agent import agent_manager
        agent_names = await agent_manager.list()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas palette: agent registry unavailable: {exc}")

    specs = _structural_specs(agent_names)

    for name in agent_names:
        try:
            from agentevolver.agent import agent_manager
            info = await agent_manager.get_info(name)
            if info is not None and _is_actor_agent(info):
                specs.append(_agent_spec(name, info))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"| ⚠️ Canvas palette: skipping agent {name}: {exc}")

    try:
        from agentevolver.workflow import workflow_manager
        for name in workflow_manager.list():
            try:
                specs.append(_workflow_spec(name, workflow_manager.get(name)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️ Canvas palette: skipping workflow {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas palette: workflow registry unavailable: {exc}")

    return specs


async def _roster(kind: str) -> List[Dict[str, str]]:
    """Selectable capabilities of one kind: [{name, description}]. Agents are
    restricted to actors (the same set the palette shows)."""
    try:
        if kind == "tools":
            from agentevolver.tool import tool_manager
            names, get_info = await tool_manager.list(), tool_manager.get_info
        elif kind == "skills":
            from agentevolver.skill import skill_manager
            names, get_info = await skill_manager.list(), skill_manager.get_info
        elif kind == "connectors":
            from agentevolver.connector import connector_manager
            names, get_info = await connector_manager.list(), connector_manager.get_info
        elif kind == "environments":
            from agentevolver.environment import environment_manager
            names, get_info = await environment_manager.list(), environment_manager.get_info
        elif kind == "agents":
            from agentevolver.agent import agent_manager
            names, get_info = await agent_manager.list(), agent_manager.get_info
        else:
            return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"| ⚠️ Canvas roster: {kind} unavailable: {exc}")
        return []

    out: List[Dict[str, str]] = []
    for name in names:
        try:
            info = await get_info(name) if inspect.iscoroutinefunction(get_info) else get_info(name)
        except Exception:  # noqa: BLE001
            info = None
        if kind == "agents" and not _is_actor_agent(info):
            continue
        out.append({"name": name, "description": str(getattr(info, "description", "") or "")})
    return sorted(out, key=lambda item: item["name"])


async def build_mounts() -> Dict[str, List[Dict[str, str]]]:
    """Global capability rosters the agent capability picker selects from."""
    return {kind: await _roster(kind) for kind in AGENT_MOUNT_KINDS}


__all__ = ["AGENT_MOUNT_KINDS", "build_catalog", "build_mounts"]
