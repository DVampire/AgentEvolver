"""Capability discovery, deferred selection, schema assembly, and dispatch routing.

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
import re
from collections import OrderedDict
from typing import Any, Dict, List, Sequence, Tuple

from agentevolver.capability import MOUNTED_TYPES
from agentevolver.logger import logger
from agentevolver.tool.types import Tool

SEARCH_NAME = "search_capabilities"
CORE_NAMES = frozenset({
    "done_tool", "bash_tool", "apply_patch_tool", "read_file_tool", "write_file_tool",
    "edit_file_tool", "grep_search_tool", "glob_search_tool", "list_dir_tool",
})

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": SEARCH_NAME,
        "description": (
            "Search the mounted tool/skill/connector/plugin/agent/workflow catalog. "
            "Matching capabilities become callable on the next step. Use this when a "
            "needed capability is not already in the native tool list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Desired capability or task."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

_MAX_CATALOGS = 512
_CATALOGS: "OrderedDict[Tuple[str, str], List[Any]]" = OrderedDict()


def remember_catalog(ctx: Any, agent_name: str, pairs: Sequence[Any]) -> None:
    key = (str(getattr(ctx, "id", "") or ""), agent_name)
    _CATALOGS[key] = list(pairs)
    _CATALOGS.move_to_end(key)
    while len(_CATALOGS) > _MAX_CATALOGS:
        _CATALOGS.popitem(last=False)


def catalog(ctx: Any, agent_name: str) -> List[Any]:
    return list(_CATALOGS.get((str(getattr(ctx, "id", "") or ""), agent_name), []))


def forget(ctx: Any, agent_name: str) -> None:
    """Drop one run's deferred catalog when its agent concludes."""
    _CATALOGS.pop((str(getattr(ctx, "id", "") or ""), agent_name), None)
    extra = getattr(ctx, "extra", None)
    if extra is not None:
        revisions = extra.get("_capability_catalog_revisions") or {}
        revisions.pop(agent_name, None)
        if not revisions:
            extra.pop("_capability_catalog_revisions", None)


def _allowlist_fingerprint(extra: Any) -> str:
    """A stable digest of the capability scope this context grants.

    Part of the catalog's cache key, so a scope change rebuilds it. Sorted per key so
    the same grant written in a different order is the same fingerprint.
    """
    if not isinstance(extra, dict):
        return ""
    parts = []
    for key in sorted(extra):
        if not str(key).endswith("_allowlist"):
            continue
        value = extra[key]
        if isinstance(value, (list, tuple, set)):
            parts.append(f"{key}={','.join(sorted(str(item) for item in value))}")
    return "|".join(parts)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", str(value).lower()))


def _loaded(ctx: Any, agent_name: str) -> List[str]:
    extra = getattr(ctx, "extra", None)
    if extra is None:
        return []
    stores = extra.setdefault("loaded_capabilities", {})
    return stores.setdefault(agent_name, [])


def _metadata(item: Any) -> Tuple[str, str, Tuple[Any, ...]]:
    """Read either a metadata entry or the legacy ``(schema, route)`` pair."""
    if isinstance(item, dict) and "route" in item:
        return (
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            tuple(item.get("route") or ()),
        )
    fc, route = item
    fn = fc.get("function", {})
    return str(fn.get("name") or ""), str(fn.get("description") or ""), tuple(route)


def select(
    pairs: Sequence[Any], *, ctx: Any, agent_name: str, threshold: int,
) -> Tuple[List[Any], bool]:
    """Return the stable core + session-loaded schemas when a catalog is large."""
    if threshold <= 0 or len(pairs) <= threshold:
        return list(pairs), False
    loaded = set(_loaded(ctx, agent_name))
    chosen = [pair for pair in pairs if _metadata(pair)[0] in CORE_NAMES | loaded]
    if pairs and isinstance(pairs[0], dict):
        chosen.append({
            "name": SEARCH_NAME,
            "description": SEARCH_SCHEMA["function"]["description"],
            "route": ("capability_search",),
            "schema": SEARCH_SCHEMA,
        })
    else:
        chosen.append((SEARCH_SCHEMA, ("capability_search",)))
    return chosen, True


def search(
    pairs: Sequence[Any], *, ctx: Any, agent_name: str, query: str, limit: int = 6,
) -> str:
    """Rank catalog entries and expose matching schemas on the next request."""
    if not str(query).strip():
        return "Capability search requires a non-empty query; no schemas were loaded."
    query_tokens = _tokens(query)
    ranked = []
    for item in pairs:
        name, description, route = _metadata(item)
        haystack = _tokens(f"{name} {description} {' '.join(map(str, route))}")
        ranked.append((
            int(str(query).lower() in f"{name} {description}".lower()),
            len(query_tokens & haystack), name, description, route,
        ))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    count = min(12, max(1, int(limit or 6)))
    matches = [item for item in ranked if item[0] or item[1]][:count]
    if not matches:
        return (
            f"No capability schemas matched {str(query).strip()!r}; no schemas were "
            "loaded. Try a tool name, domain term, or a more specific task description."
        )
    loaded = _loaded(ctx, agent_name)
    for _, _, name, _, _ in matches:
        if name and name not in loaded:
            loaded.append(name)
    lines = [f"Loaded {len(matches)} matching capability schema(s) for the next step:"]
    lines.extend(
        f"- {name} [{route[0]}]: {description}"
        for _, _, name, description, route in matches
    )
    return "\n".join(lines)

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
    metadata = catalog(ctx, agent_name)
    from agentevolver.extension import extension_manager

    # The catalog is cached against everything that can change what belongs in it: the
    # extension registry's revision, and the scope this agent has been granted. Keying
    # on the revision alone meant a grant made mid-run — handing a newly evolved
    # component to an agent whose class default excluded it — was ignored until some
    # unrelated registration happened to bump the revision.
    revision = (
        extension_manager.capability_revision,
        _allowlist_fingerprint(extra),
    )
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


__all__ = [
    "SEARCH_NAME", "SEARCH_SCHEMA", "assemble_native_tools", "catalog", "forget",
    "remember_catalog", "search", "select", "_SchemaTool",
]
