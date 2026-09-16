"""Which component types the slash commands work on, and why it is not a list.

`/inspect`, `/copy`, `/unregister`, `/rollback` and `/deprecate` all gated on one
hand-written `KNOWN_TYPES` holding six entries, and `get_manager` had a branch for the same
six. So `/inspect workflow x`, `/copy plugin y` and `/rollback memory z` answered "Unknown
type" for components this framework generates, promotes and registers — and the comment
directly beneath that list recorded the identical fix being made to the list below it,
which is how long the two had been out of step.

The types now come from the capability table. Eligibility comes from what a command needs:
`/rollback` calls `restore`, and `plugin` and `memory` do not have one. A fixed list can
express that only by omitting them from everything, which is exactly what it did — so a
type was unavailable for four commands because it was unavailable for the fifth.
"""

from __future__ import annotations

import pytest

from agentevolver.capability.types import COMPONENT_TYPES
from agentevolver.command.default._helpers import (
    get_manager,
    known_types,
    types_supporting,
)

RECOVERED = ["workflow", "memory", "plugin"]


def test_every_component_type_is_known():
    """Known and evolvable are the same set, plus `prompt`.

    A type the agents can build but the commands cannot address is one you can generate
    and then not inspect, copy or unregister.
    """
    assert set(known_types()) == {e.type for e in COMPONENT_TYPES} | {"prompt"}


@pytest.mark.parametrize("module", RECOVERED)
@pytest.mark.parametrize("method", ["get_info", "copy", "unregister"])
def test_the_three_missing_types_are_addressable(module, method):
    """The regression, named type by type and command by command."""
    assert module in types_supporting(method)


@pytest.mark.parametrize("module", [e.type for e in COMPONENT_TYPES] + ["prompt"])
def test_every_known_type_resolves_to_a_manager(module):
    """`get_manager` was a branch per type and had six. Now it reads the capability table,
    where each type already carries its manager as a callable."""
    assert get_manager(module) is not None, f"{module} has no manager"


def test_an_unknown_type_has_no_manager():
    assert get_manager("widget") is None


def test_rollback_excludes_only_the_types_that_cannot_restore():
    """The distinction a single list cannot draw.

    `plugin` and `memory` have no `restore`, so rollback genuinely does not apply to them
    — but that is a fact about rollback, not about the types, and it must not remove them
    from `/inspect` as well.
    """
    restorable = types_supporting("restore")
    assert "workflow" in restorable
    assert "plugin" not in restorable and "memory" not in restorable
    for module in ("plugin", "memory"):
        assert module in types_supporting("get_info"), (
            f"{module} lost inspect because it cannot roll back"
        )


def test_a_command_needing_two_things_requires_both():
    """`types_supporting` is an intersection, so a command can state its full precondition."""
    both = types_supporting("get_info", "restore")
    assert set(both) == set(types_supporting("get_info")) & set(types_supporting("restore"))


@pytest.mark.asyncio
async def test_rollback_on_a_type_it_cannot_restore_says_so():
    """The message a person reads when they try it.

    "Unknown type 'plugin'" is wrong twice over — plugins are a type, and the real reason
    is that rollback has nothing to call. An `AttributeError` from inside the command would
    be worse still.
    """
    from agentevolver.command.default.rollback import RollbackCommand

    result = await RollbackCommand()(["plugin", "text_metrics", "1.0.0"])
    assert not result.success
    assert "rollback is not available" in result.message
    assert "Unknown type" not in result.message


@pytest.mark.asyncio
async def test_inspect_reaches_a_type_the_old_list_refused():
    """End of the path, not just the helper: the command itself accepts `workflow` now.

    The name does not exist, so the answer is "not found" — which is the point. Before,
    the command never got as far as looking.
    """
    from agentevolver.command.default.inspect_cmd import InspectCommand

    result = await InspectCommand()(["workflow", "no_such_workflow"])
    assert not result.success
    assert "not found" in result.message
    assert "does not support" not in result.message


# --------------------------------------------------------------------------- #
# The managers do not agree on being async
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_synchronous_manager_method_is_awaited_correctly():
    """`workflow_manager` is synchronous throughout; the other seven await.

    A command dispatching by type meets both, and awaiting unconditionally fails with
    `TypeError: object NoneType can't be used in 'await' expression` — a message naming
    neither the manager nor the method. This is the second place the same disagreement has
    had to be absorbed; `Agent._get_capability_context` does it for `get_instruction`.
    """
    from agentevolver.command.default._helpers import call

    class _Sync:
        def get_info(self, name):
            return {"name": name, "sync": True}

    class _Async:
        async def get_info(self, name):
            return {"name": name, "sync": False}

    assert (await call(_Sync(), "get_info", "x"))["sync"] is True
    assert (await call(_Async(), "get_info", "x"))["sync"] is False


def test_which_managers_are_synchronous_is_recorded():
    """Written down so the inconsistency is visible rather than rediscovered.

    Not asserted as desirable — it is asserted as *true*, so that a manager quietly
    changing shape shows up here instead of as a TypeError inside a command. If workflow
    is ever made async, this test is the one that says so.
    """
    import inspect

    from agentevolver.workflow import workflow_manager

    for method in ("list", "get_info", "copy", "restore", "unregister"):
        assert not inspect.iscoroutinefunction(getattr(workflow_manager, method)), (
            f"workflow_manager.{method} is now a coroutine; the other managers agree with "
            f"it, so `call()` may no longer be needed"
        )
