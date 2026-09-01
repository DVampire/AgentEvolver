"""Native tool-calling assembly — compose every capability into one flat tool list.

For the native tool-calling run loop (see ``Agent._think`` / ``Agent._dispatch``),
the model must see all of the agent's capabilities as functions in a single
``tools`` list. Each capability MANAGER owns the projection of its own entities
into native function-calling schemas (``*.function_callings(allowlist, types)``);
this module only COMPOSES those per-manager outputs and builds one routing table
(function name → owning manager) so a returned tool_call can be dispatched back.

Which managers to ask comes from :data:`MOUNTED_TYPES` rather than from a list
here. The two used to be separate, and the way that failed is the way it always
does: a capability type existed, was registered, was callable by name — and was
absent from the model's tool list, because this file had not been told about it.

No renaming happens here. Entity names already carry their type in the name
(``bash_tool`` / ``done_tool`` / ``general_agent`` / ``self_evolving_skill``), so the
raw name is used verbatim. ``done_tool`` is an ordinary registered tool, so it
arrives through ``tool_manager`` like any other — there is no synthetic ``done``.

Each schema is carried by a schema-only ``_SchemaTool`` (a ``Tool`` subclass) so the
existing per-provider ``serialize_tools`` — which reads ``tool.function_calling`` —
work unchanged. The shim is never executed; the run loop routes tool_calls back to
the real manager by name via the routing table.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Tuple

from agentevolver.capability import MOUNTED_TYPES
from agentevolver.logger import logger
from agentevolver.tool.types import Tool

# Routing table value: a tuple describing how to dispatch a tool_call by name:
#   (capability_type, name) for something addressed by one name, and
#   (capability_type, name, member) for a container's member —
#   ("connector", name, action) | ("environment", name, action) | ("plugin", name, tool)
Route = Tuple[Any, ...]


class _SchemaTool(Tool):
    """Schema-only shim carrying one capability's ``function_calling`` for the model."""

    async def __call__(self, **kwargs):  # pragma: no cover - never invoked
        raise RuntimeError("schema-only tool shim is not directly callable")


def _shim(
    fc: Dict[str, Any], *, mutates: Any = None, programmatic: bool = False,
) -> _SchemaTool:
    """Wrap one function-calling dict in a schema-only ``_SchemaTool`` so serialization
    can read ``tool.function_calling`` uniformly; the shim itself is never executed."""
    fn = fc.get("function", {})
    return _SchemaTool(
        name=fn.get("name", ""), description=fn.get("description", ""),
        function_calling=fc, mutates=mutates,
        metadata={"programmatic": bool(programmatic)},
    )


def _projects(capability_type: str, agent: Any, extra: Dict[str, Any], include_agents: bool) -> bool:
    """Whether this run projects a capability type at all.

    Three types are opt-in rather than resident, each for its own reason:

    * ``agent`` — sub-agent dispatch belongs to an orchestrator (MetaAgent).
    * ``workflow`` — a read-only Workflow evaluator must be able to execute its
      target without also gaining arbitrary sub-agent delegation, so this is a
      separate seam from ``agent`` rather than the same flag.
    * ``plugin`` — the registry holds hundreds of tools for services most runs
      never touch, so one reaches a model only when a run names it.
    """
    if capability_type == "agent":
        return include_agents
    if capability_type == "workflow":
        return (include_agents
                or (hasattr(agent, "_include_workflows") and agent._include_workflows())
                or bool(extra.get("workflow_allowlist")))  # a canvas "Tool Mode" mount opts in
    if capability_type == "plugin":
        return bool(extra.get("plugin_allowlist"))
    return True


def _projection_kwargs(capability_type: str, agent: Any) -> Dict[str, Any]:
    """The per-type arguments beyond ``allowlist`` that this run needs.

    ``skill`` honours the agent's allowed skill types — the guardrail that keeps
    worker SOPs out of the MetaAgent and orchestration recipes out of workers —
    and ``agent`` must not project the caller as one of its own callables.
    """
    if capability_type == "skill" and hasattr(agent, "_allowed_skill_types"):
        return {"types": list(agent._allowed_skill_types())}
    if capability_type == "agent":
        return {"exclude": getattr(agent, "name", None)}
    return {}


async def _resolved(value):
    return await value if inspect.isawaitable(value) else value


async def _metadata_catalog(
    agent: Any, extra: Dict[str, Any], include_agents: bool,
) -> List[Dict[str, Any]]:
    """Enumerate names/descriptions/routes without constructing argument schemas."""
    catalog: List[Dict[str, Any]] = []
    for entry in MOUNTED_TYPES:
        if not _projects(entry.type, agent, extra, include_agents):
            continue
        manager = entry.manager()
        allowlist = extra.get(f"{entry.type}_allowlist")
        try:
            names = list(allowlist) if allowlist is not None else list(
                await _resolved(manager.list())
            )
            filters = _projection_kwargs(entry.type, agent)
            for name in names:
                if entry.type == "agent" and name == filters.get("exclude"):
                    continue
                info = await _resolved(manager.get_info(name))
                if info is None:
                    continue
                types = set(filters.get("types") or [])
                declared_type = getattr(info, "type", None)
                if types and declared_type:
                    available = (
                        {declared_type} if isinstance(declared_type, str)
                        else set(declared_type)
                    )
                    if not (types & available):
                        continue

                if entry.type == "connector":
                    for action in getattr(info, "actions", None) or []:
                        description = (
                            (getattr(info, "action_descriptions", None) or {}).get(action)
                            or getattr(info, "description", "")
                            or action
                        )
                        catalog.append({
                            "name": f"{name}__{action}",
                            "description": description,
                            "route": (entry.type, name, action),
                        })
                elif entry.type == "environment":
                    for action, action_info in (getattr(info, "actions", None) or {}).items():
                        catalog.append({
                            "name": f"{name}__{action}",
                            "description": getattr(action_info, "description", "") or action,
                            "route": (entry.type, name, action),
                        })
                elif entry.type == "plugin":
                    plugin_type = getattr(getattr(info, "instance", None), "type", None)
                    if types and plugin_type and plugin_type not in types:
                        continue
                    for tool in (getattr(info, "tools", None) or {}).values():
                        if not getattr(tool, "implemented", False):
                            continue
                        catalog.append({
                            "name": f"{name}__{tool.name}",
                            "description": tool.description or tool.display_name or tool.name,
                            "route": (entry.type, name, tool.name),
                        })
                else:
                    catalog.append({
                        "name": str(name),
                        "description": getattr(info, "description", "") or str(name),
                        "route": (entry.type, name),
                    })
        except Exception as error:  # one source cannot poison the whole index
            # Third-party/legacy managers may expose only the older schema-first API.
            # Keep them callable while first-party managers stay metadata-only here.
            try:
                discovered = await manager.function_callings(
                    allowlist, **_projection_kwargs(entry.type, agent),
                )
                for schema, route in discovered:
                    function = schema.get("function", {}) if isinstance(schema, dict) else {}
                    if function.get("name"):
                        catalog.append({
                            "name": function["name"],
                            "description": function.get("description", ""),
                            "route": tuple(route),
                            "schema": schema,
                        })
            except Exception:
                logger.warning(
                    f"| ⚠️ Capability metadata discovery failed for {entry.type}: {error}; "
                    "the remaining capability types stay available"
                )
    return catalog


async def assemble_native_tools(
    agent: Any, ctx: Any, *, include_agents: bool = False
) -> Tuple[List[_SchemaTool], Dict[str, Route]]:
    """Compose the agent's capabilities into (tools, routing).

    ``tools`` is a flat list of ``_SchemaTool`` to pass as ``input["tools"]``.
    ``routing`` maps each function name to its dispatch descriptor (see ``Route``).

    Every per-entity schema comes from that entity's OWN manager
    (``*.function_callings``); this function only concatenates them and keys the
    routing table by name. ``include_agents=True`` also projects every registered
    sub-agent (except the caller) as a callable — used by MetaAgent, which dispatches
    agents; ordinary sub-agents leave it off.
    """
    extra = getattr(ctx, "extra", None)
    if extra is None:
        extra = {}
    agent_name = getattr(agent, "name", "agent")
    from agentevolver.agent.capability_index import catalog, remember_catalog, select

    metadata = catalog(ctx, agent_name)
    from agentevolver.extension import extension_manager

    revision = extension_manager.capability_revision
    revisions = extra.setdefault("_capability_catalog_revisions", {})
    if not metadata or revisions.get(agent_name) != revision:
        # Freeze the lightweight index for one run. Full JSON schemas are built only
        # for the stable core and names the session has selected. A hot extension bumps
        # the registry revision, causing exactly one rebuild so the new capability is
        # callable on the next turn without re-discovering unchanged catalogs every step.
        metadata = await _metadata_catalog(agent, extra, include_agents)
        remember_catalog(ctx, agent_name, metadata)
        revisions[agent_name] = revision

    selected, _deferred = select(
        metadata,
        ctx=ctx,
        agent_name=agent_name,
        threshold=int(getattr(agent, "defer_capabilities_after", 40) or 0),
    )

    managers = {entry.type: entry.manager() for entry in MOUNTED_TYPES}
    pairs: List[Tuple[Dict[str, Any], Route]] = []
    for item in selected:
        if not isinstance(item, dict):
            # Backward compatibility for a catalog remembered by an older in-process
            # caller during a hot reload.
            schema, route = item
            pairs.append((schema, tuple(route)))
            continue
        route = tuple(item.get("route") or ())
        schema = item.get("schema")
        if schema is None and route:
            try:
                schema = await _resolved(managers[route[0]].get_schema(
                    route[1],
                    action=route[2] if len(route) > 2 else None,
                    format="json",
                ))
            except Exception as error:
                logger.warning(
                    f"| ⚠️ Capability schema load failed for {item.get('name')}: {error}"
                )
                continue
        if (
            isinstance(schema, dict)
            and isinstance(schema.get("function"), dict)
            and schema["function"].get("name")
        ):
            pairs.append((schema, route))

    tools: List[_SchemaTool] = []
    for fc, route in pairs:
        # Hosted programs are deliberately narrower than direct calls. A Tool must be
        # both read-only and explicitly opt in through metadata; writes,
        # approval-sensitive capabilities, agents, skills and connectors remain direct
        # so the authorization boundary is visible to the ordinary Agent loop. The local
        # batch_call fallback applies the same guard on every nested call.
        mutates = None
        declared_programmatic = False
        if route and route[0] == "tool":
            try:
                from agentevolver.tool import tool_manager
                info = await tool_manager.get_info(route[1])
                mutates = getattr(info, "mutates", None) if info is not None else None
                declared_programmatic = bool(
                    ((getattr(info, "metadata", None) or {}).get("programmatic"))
                    if info is not None else False
                )
            except Exception:
                mutates = None
        tools.append(_shim(
            fc,
            mutates=mutates,
            programmatic=bool(
                route and route[0] == "tool" and mutates is False
                and declared_programmatic
            ),
        ))
    routing = {fc["function"]["name"]: route for fc, route in pairs}
    return tools, routing


__all__ = ["assemble_native_tools", "_SchemaTool"]
