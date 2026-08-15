"""Everything registered through one scope comes back out, or the caller is told what did not.

Each registry here owns one kind of thing and removes one by name. Nothing owned the
**set** a single contributor installed, and that gap quietly decided the extension model:
`ExtensionManager` unregistered from the one registry named by a component's `module`
field, which was correct only because a component was not allowed to be more than one
thing. A contributor could not offer two tools plus a prompt — not because anything
rejected it, but because nothing could take it back out.

Three properties carry the whole thing, and each has a failure mode that looks like
success. Disposing in registration order tears down a dependency before its dependent.
Stopping at the first failure leaves a set nobody can describe. And a dispose that runs
twice unregisters a name some later contributor has since claimed — which reads as a clean
cleanup and corrupts the registry.
"""

from __future__ import annotations

import asyncio

import pytest

from agentevolver.scope import Scope


class FakeManager:
    """A registry with this project's shape: `register(item)` / `unregister(name)`."""

    def __init__(self, label: str = "Fake", fail_on: str | None = None):
        self.label = label
        self.items: dict[str, object] = {}
        self.removed: list[str] = []
        self._fail_on = fail_on

    async def register(self, item, **_kwargs):
        self.items[item.name] = item
        return item

    async def unregister(self, name: str) -> bool:
        if name == self._fail_on:
            raise RuntimeError(f"{self.label} refuses to remove {name}")
        self.removed.append(name)
        return self.items.pop(name, None) is not None

    def __repr__(self) -> str:
        return f"<{self.label}>"


class Item:
    def __init__(self, name: str):
        self.name = name


# --------------------------------------------------------------------------- #
# What a scope takes on
# --------------------------------------------------------------------------- #
def test_one_scope_removes_registrations_across_several_registries():
    """The capability the extension model was missing.

    A contributor that is a tool *and* a prompt is unremovable without this: unloading it
    means knowing every registry it touched, which is exactly what nothing recorded.
    """
    tools, prompts = FakeManager("Tools"), FakeManager("Prompts")

    async def run():
        scope = Scope(name="ext:demo")
        await scope.register(tools, Item("search"))
        await scope.register(tools, Item("fetch"))
        await scope.register(prompts, Item("search_prompt"))
        assert len(scope) == 3

        assert await scope.dispose() == []

    asyncio.run(run())

    assert tools.items == {} and prompts.items == {}


def test_a_manager_without_unregister_is_refused_at_registration():
    """Refused when the caller can still act, not at disposal when it cannot.

    Discovering an unremovable registration during teardown means the contributor is
    already installed and there is nothing useful left to do about it.
    """
    class WriteOnly:
        async def register(self, item, **_):
            return item

    async def run():
        with pytest.raises(TypeError, match="unregister"):
            await Scope(name="s").register(WriteOnly(), Item("x"))

    asyncio.run(run())


def test_something_that_cannot_be_named_is_refused():
    """The scope stores a name to undo by. Guessing one would unregister the wrong thing."""
    async def run():
        with pytest.raises(ValueError, match="name="):
            await Scope(name="s").register(FakeManager(), object())

    asyncio.run(run())


def test_a_scope_can_own_anything_reversible_not_only_registry_entries():
    """`add` is the lower-level door: a listener removal, a temp file, a started process.

    Without it a contributor's non-registry side effects stay outside the one handle that
    is supposed to represent it.
    """
    undone = []

    async def run():
        scope = Scope(name="s")
        scope.add("listener", lambda: undone.append("listener"))
        await scope.dispose()

    asyncio.run(run())
    assert undone == ["listener"]


# --------------------------------------------------------------------------- #
# How it gives them back
# --------------------------------------------------------------------------- #
def test_disposal_runs_newest_first():
    """Reverse order, because later registrations may depend on earlier ones.

    A tool registered against a connector has to go before the connector does; tearing
    down in registration order removes the connector while the tool still points at it.
    """
    order = []

    async def run():
        scope = Scope(name="s")
        for label in ("connector", "tool", "prompt"):
            scope.add(label, lambda captured=label: order.append(captured))
        await scope.dispose()

    asyncio.run(run())
    assert order == ["prompt", "tool", "connector"]


def test_one_failure_does_not_abandon_the_rest():
    """Aborting partway leaves a set nobody can describe.

    Some removed, some not, and no record of which — the caller cannot retry, cannot
    report, and cannot even enumerate what is still live.
    """
    tools = FakeManager("Tools", fail_on="stubborn")

    async def run():
        scope = Scope(name="s")
        await scope.register(tools, Item("first"))
        await scope.register(tools, Item("stubborn"))
        await scope.register(tools, Item("last"))

        return await scope.dispose()

    failed = asyncio.run(run())

    assert failed == ["FakeManager:stubborn"]
    # The two that could go, went — in reverse order, around the one that could not.
    assert tools.removed == ["last", "first"]


def test_the_failure_report_names_what_is_still_installed():
    """A count would say something went wrong; the label says what to go look at."""
    tools = FakeManager("Tools", fail_on="stuck")

    async def run():
        scope = Scope(name="s")
        await scope.register(tools, Item("stuck"))
        return await scope.dispose()

    assert asyncio.run(run()) == ["FakeManager:stuck"]


def test_disposing_twice_does_not_remove_anything_a_second_time():
    """The quiet corruption this prevents.

    By the second dispose another contributor may hold that name, and unregistering it
    looks exactly like a successful cleanup — from the outside, and in the log.
    """
    tools = FakeManager("Tools")

    async def run():
        scope = Scope(name="s")
        await scope.register(tools, Item("search"))
        await scope.dispose()
        assert await scope.dispose() == []

    asyncio.run(run())
    assert tools.removed == ["search"]


def test_a_disposed_scope_refuses_new_registrations():
    """Anything added afterwards would never be undone, and nothing would say so."""
    async def run():
        scope = Scope(name="s")
        await scope.dispose()
        with pytest.raises(RuntimeError, match="disposed"):
            scope.add("late", lambda: None)

    asyncio.run(run())


def test_a_synchronous_unregister_is_awaited_correctly():
    """`hook_manager.unregister` is sync while the other eight are async.

    A scope that assumed one or the other would either never call half of them or raise
    on the rest, and both failures happen at teardown.
    """
    removed = []

    class SyncManager:
        def register(self, item, **_):
            return item

        def unregister(self, name):
            removed.append(name)
            return True

    async def run():
        scope = Scope(name="s")
        await scope.register(SyncManager(), Item("hook"))
        await scope.dispose()

    asyncio.run(run())
    assert removed == ["hook"]


# --------------------------------------------------------------------------- #
# The registries it is built against
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module", [
    "tool", "agent", "prompt", "skill", "environment", "connector", "memory", "plugins",
])
def test_every_registry_still_spells_removal_the_same_way(module: str):
    """`unregister(name)` is the whole contract a scope needs.

    Discovered per module rather than asserted once, so a registry that renames its
    removal method fails here — where the reason is visible — rather than at the next
    extension unload, where it looks like the extension is at fault.
    """
    import importlib

    server = importlib.import_module(f"agentevolver.{module}.server")
    manager = next(
        value for name, value in vars(server).items()
        if name.endswith("_manager") and hasattr(value, "register")
    )

    assert hasattr(manager, "unregister"), f"{module} has no unregister(name)"
