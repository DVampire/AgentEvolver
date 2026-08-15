"""A registration scope: everything registered through it comes back out together.

Registries here each own one kind of thing — a tool, an agent, a prompt — and each
knows how to remove one by name. Nothing owned the *set* a single contributor
installed, so removing a contributor meant knowing every registry it had touched and
undoing each by hand. In practice nobody did: `ExtensionManager` unregistered from the
one registry named by the component's module, which was correct only because a component
was not allowed to be more than one thing.

That restriction is what a scope lifts. A contributor takes a scope, registers whatever
it needs through it, and the scope is the handle that removes all of it — in reverse
order, surviving a failure partway through, and reporting what could not be undone.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional, Union

from agentevolver.logger import logger

#: What a scope stores per registration: how to undo it, and what to call it in a report.
Undo = Callable[[], Union[None, Awaitable[None]]]


@dataclass
class ScopeEntry:
    """One reversible registration."""

    label: str
    undo: Undo


@dataclass
class Scope:
    """Owns every registration made through it, and removes them as a unit.

    Not a context manager. A scope usually outlives the function that fills it — an
    extension is registered during load and disposed at unload, on a different call
    stack — and a `with` block would suggest the opposite.
    """

    name: str
    _entries: List[ScopeEntry] = field(default_factory=list)
    _disposed: bool = False

    # ------------------------------------------------------------------
    # Filling it
    # ------------------------------------------------------------------
    def add(self, label: str, undo: Undo) -> None:
        """Record something to undo. Accepts sync or async callables.

        The lowest-level entry point: anything reversible can be parked here, including
        a listener removal or a temporary file's deletion, not only a registry entry.
        """
        if self._disposed:
            raise RuntimeError(f"scope {self.name!r} is disposed; it cannot take {label!r}")
        self._entries.append(ScopeEntry(label=label, undo=undo))

    async def register(self, manager: Any, item: Any, *, name: Optional[str] = None, **kwargs: Any) -> Any:
        """Register `item` with `manager` and remember how to remove it.

        Every registry in this project spells removal the same way — `unregister(name)` —
        so one adapter covers all of them. A manager that does not is a manager this
        cannot own, and it says so at registration rather than at disposal, when the
        caller is no longer in a position to do anything about it.
        """
        if not hasattr(manager, "unregister"):
            raise TypeError(
                f"{type(manager).__name__} has no unregister(); scope {self.name!r} cannot "
                f"take responsibility for what it registers"
            )
        # Resolved BEFORE registering, deliberately. Taking the name from the register
        # call's return value reads more permissively and fails in the worst place: the
        # item is already installed, this raises, and nothing holds a way to remove it.
        # A manager that only reveals the name on return is served by passing `name=`.
        entry_name = name or getattr(item, "name", None)
        if not entry_name:
            raise ValueError(
                f"cannot name what would be registered with {type(manager).__name__}; "
                f"pass name= so the scope can remove it"
            )
        result = manager.register(item, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        self.add(f"{type(manager).__name__}:{entry_name}", lambda: manager.unregister(entry_name))
        return result

    # ------------------------------------------------------------------
    # Emptying it
    # ------------------------------------------------------------------
    async def dispose(self) -> List[str]:
        """Undo everything, newest first. Returns the labels that could not be undone.

        **Reverse order** because later registrations may depend on earlier ones: a tool
        registered against a connector has to go before the connector does.

        **One failure does not stop the rest.** Aborting partway would leave a set nobody
        can describe — some removed, some not, and no record of which. Every failure is
        collected and returned, so the caller learns what is still installed.

        **Idempotent.** Disposing twice must not unregister a name a later contributor has
        since claimed, which is the quiet way this kind of cleanup corrupts a registry.
        """
        if self._disposed:
            return []
        self._disposed = True

        failed: List[str] = []
        for entry in reversed(self._entries):
            try:
                result = entry.undo()
                if inspect.isawaitable(result):
                    await result
            except Exception as error:  # noqa: BLE001 — the next entry still has to run
                failed.append(entry.label)
                logger.warning(f"| ⚠️ scope {self.name!r}: could not undo {entry.label}: {error}")
        self._entries.clear()
        if failed:
            logger.warning(f"| ⚠️ scope {self.name!r} disposed with {len(failed)} left installed")
        return failed

    # ------------------------------------------------------------------
    # Reading it
    # ------------------------------------------------------------------
    @property
    def disposed(self) -> bool:
        return self._disposed

    def labels(self) -> List[str]:
        """What this scope currently owns, in registration order."""
        return [entry.label for entry in self._entries]

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["Scope", "ScopeEntry", "Undo"]
