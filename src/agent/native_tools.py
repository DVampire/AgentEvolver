"""Native tool-calling assembly — project every capability into one flat tool list.

For the native tool-calling run loop (see ``Agent._think_and_act_native``), the
model must see all of the agent's capabilities as functions in a single ``tools``
list. This module projects Tool / Skill / Connector-action / Environment-action /
``done`` into canonical OpenAI function-calling schemas and returns them together
with a routing table (namespaced tool name → owning manager), so a returned
tool_call can be dispatched back to the right place.

Tools already carry an auto-generated ``function_calling`` (ToolConfig); env
actions too (ActionConfig). Skills have no schema → a permissive object schema
(decision D1). Connector actions use the connector's per-action schema when
available, else a permissive object.

Each projected function is carried by a schema-only ``_SchemaTool`` (a ``Tool``
subclass) so the existing per-provider ``serialize_tools`` — which reads
``tool.function_calling`` — work unchanged. The shim is never executed; the run
loop routes tool_calls back to the real manager by name.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from src.tool.types import Tool


class _SchemaTool(Tool):
    """Schema-only shim carrying a namespaced ``function_calling`` for one capability."""

    async def __call__(self, **kwargs):  # pragma: no cover - never invoked
        raise RuntimeError("schema-only tool shim is not directly callable")


def _fc(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Build a canonical OpenAI function-calling dict."""
    return {"type": "function", "function": {"name": name, "description": description or name, "parameters": parameters}}


def _renamed(fc: Dict[str, Any], new_name: str) -> Dict[str, Any]:
    """Clone an existing function_calling dict under a namespaced name (params kept)."""
    out = copy.deepcopy(fc or {})
    fn = out.setdefault("function", {})
    fn["name"] = new_name
    out.setdefault("type", "function")
    if "parameters" not in fn:
        fn["parameters"] = {"type": "object", "additionalProperties": True}
    return out


def _shim(fc: Dict[str, Any]) -> _SchemaTool:
    fn = fc.get("function", {})
    return _SchemaTool(name=fn.get("name", ""), description=fn.get("description", ""), function_calling=fc)


# Routing table value: tuple describing how to dispatch a tool_call by name.
Route = Tuple[Any, ...]

_DONE_FC = _fc(
    "done",
    "Signal that the task is complete and return the final result. Call this (and only this) when there is nothing left to do.",
    {
        "type": "object",
        "properties": {
            "result": {"type": "string", "description": "The final result of the task."},
            "reasoning": {"type": "string", "description": "Why the task is now complete."},
        },
        "required": ["result"],
        "additionalProperties": False,
    },
)


async def assemble_native_tools(agent: Any, ctx: Any) -> Tuple[List[_SchemaTool], Dict[str, Route]]:
    """Project the agent's capabilities into (tools, routing).

    ``tools`` is a list of ``_SchemaTool`` (each carrying a namespaced
    function_calling) to pass as ``input["tools"]``. ``routing`` maps each
    namespaced name to a dispatch descriptor consumed by the run loop:
      ("tool", name) | ("skill", name) | ("connector", name, action)
      | ("env", action) | ("finish",)
    """
    from src.tool.server import tool_manager
    from src.skill.server import skill_manager
    from src.connector.server import connector_manager

    tools: List[_SchemaTool] = []
    routing: Dict[str, Route] = {}

    def add(fc: Dict[str, Any], route_key: str, route: Route) -> None:
        tools.append(_shim(fc))
        routing[route_key] = route

    extra = getattr(ctx, "extra", None) or {}

    async def _names(allow, manager):
        if allow is not None:
            return allow
        try:
            return await manager.list()
        except Exception:
            return []

    # done — the sole completion signal in native mode
    add(_DONE_FC, "done", ("finish",))

    # tools (honor the same allowlist the prompt context uses)
    t_names = await _names(extra.get("tool_allowlist"), tool_manager)
    for n in t_names:
        try:
            info = await tool_manager.get_info(n)
        except Exception:
            info = None
        fc = getattr(info, "function_calling", None) if info else None
        if not fc:
            continue
        ns = f"tool__{n}"
        add(_renamed(fc, ns), ns, ("tool", n))

    # skills (D1: permissive object schema; honor allowlist + this agent's skill types)
    s_names = await _names(extra.get("skill_allowlist"), skill_manager)
    allowed_types = set(agent._allowed_skill_types()) if hasattr(agent, "_allowed_skill_types") else set()
    for n in s_names:
        try:
            info = await skill_manager.get_info(n)
        except Exception:
            info = None
        if not info:
            continue
        stype = getattr(info, "type", None)
        if allowed_types and stype:
            have = {stype} if isinstance(stype, str) else set(stype)
            if not (have & allowed_types):
                continue
        ns = f"skill__{n}"
        add(_fc(ns, getattr(info, "description", "") or n, {"type": "object", "additionalProperties": True}),
            ns, ("skill", n))

    # connector actions (one function per MCP action)
    c_names = await _names(extra.get("connector_allowlist"), connector_manager)
    for n in c_names:
        try:
            info = await connector_manager.get_info(n)
        except Exception:
            info = None
        if not info:
            continue
        actions = getattr(info, "actions", None) or []
        schemas = getattr(info, "action_schemas", None) or {}
        cdesc = getattr(info, "description", "") or n
        for act in actions:
            ns = f"connector__{n}__{act}"
            params = schemas.get(act) or {"type": "object", "additionalProperties": True}
            add(_fc(ns, f"{cdesc} — action '{act}'", params), ns, ("connector", n, act))

    # environment actions — hook, default none (env-bound agents override)
    if hasattr(agent, "_native_env_tools"):
        try:
            for ns, params, desc, route in await agent._native_env_tools(ctx):
                add(_fc(ns, desc, params), ns, route)
        except Exception:
            pass

    return tools, routing


__all__ = ["assemble_native_tools", "_SchemaTool"]
