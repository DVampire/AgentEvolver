"""Provider-neutral deferred capability selection for large native tool catalogs."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Sequence, Tuple

SEARCH_NAME = "search_capabilities"
CORE_NAMES = frozenset({
    "done_tool", "bash_tool", "read_file_tool", "write_file_tool",
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
_CATALOGS: "OrderedDict[Tuple[str, str], List[Tuple[Dict[str, Any], Tuple[Any, ...]]]]" = OrderedDict()


def remember_catalog(
    ctx: Any, agent_name: str,
    pairs: Sequence[Tuple[Dict[str, Any], Tuple[Any, ...]]],
) -> None:
    key = (str(getattr(ctx, "id", "") or ""), agent_name)
    _CATALOGS[key] = list(pairs)
    _CATALOGS.move_to_end(key)
    while len(_CATALOGS) > _MAX_CATALOGS:
        _CATALOGS.popitem(last=False)


def catalog(ctx: Any, agent_name: str):
    return list(_CATALOGS.get(
        (str(getattr(ctx, "id", "") or ""), agent_name), []
    ))


def forget(ctx: Any, agent_name: str) -> None:
    """Drop one run's deferred catalog when its agent concludes."""
    _CATALOGS.pop((str(getattr(ctx, "id", "") or ""), agent_name), None)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", str(value).lower()))


def _loaded(ctx: Any, agent_name: str) -> List[str]:
    extra = getattr(ctx, "extra", None)
    if extra is None:
        return []
    stores = extra.setdefault("loaded_capabilities", {})
    return stores.setdefault(agent_name, [])


def select(
    pairs: Sequence[Tuple[Dict[str, Any], Tuple[Any, ...]]],
    *,
    ctx: Any,
    agent_name: str,
    threshold: int,
) -> Tuple[List[Tuple[Dict[str, Any], Tuple[Any, ...]]], bool]:
    """Return the stable core + session-loaded schemas when a catalog is large."""
    if threshold <= 0 or len(pairs) <= threshold:
        return list(pairs), False
    loaded = set(_loaded(ctx, agent_name))
    chosen = [
        pair for pair in pairs
        if pair[0].get("function", {}).get("name") in CORE_NAMES | loaded
    ]
    chosen.append((SEARCH_SCHEMA, ("capability_search",)))
    return chosen, True


def search(
    pairs: Sequence[Tuple[Dict[str, Any], Tuple[Any, ...]]],
    *,
    ctx: Any,
    agent_name: str,
    query: str,
    limit: int = 6,
) -> str:
    """Rank catalog entries and mark the returned schemas for the next request."""
    if not str(query).strip():
        return "Capability search requires a non-empty query; no schemas were loaded."
    query_tokens = _tokens(query)
    ranked = []
    for fc, route in pairs:
        fn = fc.get("function", {})
        name = str(fn.get("name") or "")
        description = str(fn.get("description") or "")
        haystack = _tokens(f"{name} {description} {' '.join(map(str, route))}")
        overlap = len(query_tokens & haystack)
        substring = int(str(query).lower() in f"{name} {description}".lower())
        ranked.append((substring, overlap, name, description, route))
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
    lines = [
        f"Loaded {len(matches)} matching capability schema(s) for the next step:"
    ]
    for _, _, name, description, route in matches:
        lines.append(
            f"- {name} [{route[0]}]: {description[:240]}"
        )
    return "\n".join(lines)


__all__ = [
    "SEARCH_NAME", "SEARCH_SCHEMA", "catalog", "forget", "remember_catalog", "select", "search",
]
