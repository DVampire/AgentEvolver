"""The hook system: event names, the manager, and the built-in hooks.

``HookEvent`` is re-exported eagerly and everything else on demand. The enum has no
dependencies, but this package's manager and hooks reach the message, session and tool
packages, and those reach back — so importing the package to name an event used to close
a cycle. Two call sites hit exactly that and settled for raw event strings, which do not
fail when they drift. Resolving the heavy names on first access keeps every
``from agentevolver.hook import ...`` working while leaving ``hook.events`` importable
from any layer.
"""

from .events import HookEvent

_LAZY = {
    "HookConfig": ".context",
    "HookContextManager": ".context",
    "hook_manager": ".server",
    "Hook": ".types",
    "HookContext": ".types",
    "HookDecision": ".types",
    "HookResult": ".types",
    "CompactHook": ".default",
    "ConstraintHook": ".default",
    "PlanModeHook": ".default",
    "ProjectMemoryHook": ".default",
    "RegistrationHook": ".default",
    "TraceHook": ".default",
    "TrajectoryHook": ".default",
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)


def __dir__():
    return sorted(__all__)


__all__ = ["HookEvent", *sorted(_LAZY)]
