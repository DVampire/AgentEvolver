"""Shared helpers for commands — which types a command applies to, and their managers.

Both answers used to be written out by hand here, and both were wrong in the same way.
``KNOWN_TYPES`` listed six types and ``get_manager`` had a branch for the same six, so
``/inspect workflow ...``, ``/copy plugin ...`` and ``/rollback memory ...`` all answered
"Unknown type" for components the framework generates, promotes and registers. The comment
below ``AGENT_BACKED_TYPES`` already recorded this exact fix being made *to that list* —
and the list above it was left alone.

So neither is written out any more. The types come from the capability table, and a
command's eligibility comes from what its manager can actually do: ``/rollback`` needs
``restore``, and ``plugin`` and ``memory`` have no ``restore``, which a fixed list can
express only by omitting them from everything.
"""

from inspect import isawaitable
from typing import Any, List, Optional

# Types the generate / evaluate / optimize agents can work on (SKILL commands dispatch to
# those three). Taken from the extension tree rather than written out again: this list was
# short by `workflow`, `memory` and `plugin`, so `/create workflow ...` was refused for a
# type the framework has always been able to build.
from agentevolver.extension import EVOLVABLE_MODULES

AGENT_BACKED_TYPES = list(EVOLVABLE_MODULES)


def known_types() -> List[str]:
    """Every versioned type: the eight components, plus ``prompt``.

    ``prompt`` is not a component — it is not generated or evolved on its own — but it is
    versioned, copied and rolled back beside the agent it belongs to, so the commands
    address it like one.
    """
    from agentevolver.capability.types import COMPONENT_TYPES

    return [entry.type for entry in COMPONENT_TYPES] + ["prompt"]


def types_supporting(*methods: str) -> List[str]:
    """The known types whose manager implements all of ``methods``.

    A command's real precondition, rather than a list someone maintains. ``/rollback``
    calls ``restore``; ``plugin`` and ``memory`` do not have one, and the honest answer to
    ``/rollback plugin x`` is that rollback is not available for plugins — not that
    plugins are an unknown type, and not an ``AttributeError`` from inside the command.
    """
    eligible = []
    for name in known_types():
        manager = get_manager(name)
        if manager is not None and all(hasattr(manager, method) for method in methods):
            eligible.append(name)
    return eligible


def get_manager(type_name: str) -> Optional[Any]:
    """Return the global manager for a type, or ``None`` if there is no such type.

    Read off the capability table, which holds each type's manager as a callable for
    exactly this reason — importing them here would close a cycle through most of the
    package. A branch per type stood here instead, and it was missing three.
    """
    if type_name == "prompt":
        from agentevolver.prompt.server import prompt_manager
        return prompt_manager

    from agentevolver.capability.types import component_type

    entry = component_type(type_name)
    if entry is None:
        return None
    try:
        return entry.manager()
    except Exception:       # noqa: BLE001 — an unimportable manager is an unknown type here
        return None


async def call(manager: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """Call one manager method, whether or not that manager made it a coroutine.

    The eight managers do not agree. `workflow_manager` is synchronous throughout — its
    registry is in memory and never touches disk during a lookup — while every other
    manager awaits, and `plugin` and `memory` are synchronous for `copy` alone. A command
    that dispatches by type meets all of them, and awaiting unconditionally fails with
    `TypeError: object NoneType can't be used in 'await' expression`, which names neither
    the manager nor the method.

    This is the second place the same disagreement has had to be absorbed: `Agent
    ._get_capability_context` does it for `get_instruction`, for the same reason and with
    the same fix. Absorbed rather than corrected because making the workflow registry
    async would change every one of its callers to fix a difference that costs one line
    here — but it is a real inconsistency, and a third occurrence is the point at which
    the interface, not the callers, is what should change.
    """
    result = getattr(manager, method)(*args, **kwargs)
    return await result if isawaitable(result) else result
