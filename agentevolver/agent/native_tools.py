"""Native tool-calling assembly — compose every capability into one flat tool list.

For the native tool-calling run loop (see ``Agent._think`` / ``Agent._dispatch``),
the model must see all of the agent's capabilities as functions in a single
``tools`` list. Each capability MANAGER owns the projection of its own entities
into native function-calling schemas (``*.function_callings(allowlist, types)``);
this module only COMPOSES those per-manager outputs and builds one routing table
(function name → owning manager) so a returned tool_call can be dispatched back.

Which managers to ask comes from :data:`CAPABILITY_TYPES` rather than from a list
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

from typing import Any, Dict, List, Tuple

from agentevolver.capability import CAPABILITY_TYPES
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


def _shim(fc: Dict[str, Any]) -> _SchemaTool:
    """Wrap one function-calling dict in a schema-only ``_SchemaTool`` so serialization
    can read ``tool.function_calling`` uniformly; the shim itself is never executed."""
    fn = fc.get("function", {})
    return _SchemaTool(name=fn.get("name", ""), description=fn.get("description", ""), function_calling=fc)


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
    extra = getattr(ctx, "extra", None) or {}
    pairs: List[Tuple[Dict[str, Any], Route]] = []

    for entry in CAPABILITY_TYPES:
        if not _projects(entry.type, agent, extra, include_agents):
            continue
        pairs += await entry.manager().function_callings(
            extra.get(f"{entry.type}_allowlist"),
            **_projection_kwargs(entry.type, agent),
        )

    tools = [_shim(fc) for fc, _ in pairs]
    routing = {fc["function"]["name"]: route for fc, route in pairs}
    return tools, routing


__all__ = ["assemble_native_tools", "_SchemaTool"]
