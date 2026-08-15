"""The two places an agent can work offer the same operations, or the gap is named.

An agent does its file and process work *somewhere*, and there are already two somewheres.
`SSHEnvironment` does `run`, `read`, `write`, `edit`, `list`, `grep`, `glob`, `remove` on a
remote host. The default tools do the same things here, reaching this machine directly.

Nothing relates them. So an operation added on one side is simply absent on the other, and
the absence is discovered the way capability gaps always are — by a model, mid-task,
finding that something it can do in one place does not exist in the other. Neither a type
checker nor any test could see it, because there was no shared contract to check against.

`environment/world.py` is that contract, and this file measures both implementations
against it. It reports rather than forbids: an environment offering seven of the eight is
useful, and the value here is that the missing one has a name.
"""

from __future__ import annotations

import pytest

from agentevolver.environment.world import (
    WORLD_OPERATIONS,
    ExecutionWorld,
    missing_operations,
)


# --------------------------------------------------------------------------- #
# The remote world
# --------------------------------------------------------------------------- #
def test_the_ssh_environment_is_a_complete_execution_world():
    """The implementation the contract was read off, so it must satisfy all of it.

    If this ever fails, the contract has grown an operation nobody implemented — which is
    the failure mode of writing a protocol from what one wishes existed rather than from
    what does.
    """
    from agentevolver.environment.default.ssh.environment import SSHEnvironment

    assert missing_operations(SSHEnvironment) == []


def test_a_complete_world_satisfies_the_protocol_without_inheriting_it():
    """Structural checking is the whole reason this is a `Protocol`.

    `SSHEnvironment` predates the contract and satisfies it already; making it inherit
    would mean editing working code to record something that is true either way — and
    would exclude any future world that cannot inherit, such as one behind an adapter.
    """
    from agentevolver.environment.default.ssh.environment import SSHEnvironment

    assert isinstance(SSHEnvironment, type)
    assert all(callable(getattr(SSHEnvironment, name, None)) for name in WORLD_OPERATIONS)


# --------------------------------------------------------------------------- #
# The local world, as the tools currently spell it
# --------------------------------------------------------------------------- #
#: Which shipped tool performs each world operation on this machine. Reaching the host
#: directly is what these do today — the mapping is the evidence that a local world exists
#: in pieces, spread across the tool layer, rather than as anything a caller can select.
LOCAL_TOOLS = {
    "run": "bash",
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
}


@pytest.mark.parametrize("operation,module", sorted(LOCAL_TOOLS.items()))
def test_the_local_world_covers_this_operation_as_a_tool(operation: str, module: str):
    """Each mapped operation has a shipped tool behind it.

    Parametrized per operation so a failure names the one that went missing, rather than
    reporting that "the local world is incomplete" and leaving the reader to find out
    which part.
    """
    import importlib

    assert importlib.import_module(f"agentevolver.tool.default.{module}")


def test_the_local_world_is_missing_operations_the_remote_one_has():
    """The finding, asserted so it cannot quietly change in either direction.

    `list`, `grep`, `glob`, and `remove` exist on a remote host and have no local tool. An
    agent working over SSH can search for a file; the same agent working here cannot, and
    nothing in the codebase said so.

    Written as an equality rather than a "these are missing" check: if a local `grep` lands,
    this test fails and someone deletes its name from the list — which is how the gap gets
    closed on purpose rather than drifting shut.
    """
    uncovered = sorted(set(WORLD_OPERATIONS) - set(LOCAL_TOOLS))

    assert uncovered == ["glob", "grep", "list", "remove"]


# --------------------------------------------------------------------------- #
# The contract itself
# --------------------------------------------------------------------------- #
def test_an_incomplete_world_is_reported_not_refused():
    """An environment offering most of the operations is still worth having.

    Refusing to load it would trade a partial world for no world at all; the point is that
    the caller can name which capability an agent will not have there.
    """
    class Partial:
        async def run(self, command, **kwargs): ...
        async def read(self, path, **kwargs): ...

    assert missing_operations(Partial) == ["write", "edit", "list", "grep", "glob", "remove"]


def test_the_report_follows_contract_order_not_alphabetical():
    """`run` and `read` before the rest, because that is the order they are declared in.

    Alphabetical order would put `edit` first and read as though the contract began there,
    which matters when the list is what a human scans to see how much of a world is missing.
    """
    class Nothing:
        pass

    assert missing_operations(Nothing) == list(WORLD_OPERATIONS)


def test_a_non_callable_attribute_does_not_count_as_an_operation():
    """A world with a `run` string satisfies `hasattr` and cannot execute anything.

    `getattr` alone is the tempting check and it accepts exactly the object that would fail
    at the moment an agent tried to use it.
    """
    class Misleading:
        run = "not a method"

    assert "run" in missing_operations(Misleading)


def test_the_protocol_is_runtime_checkable():
    """So a caller can test a candidate world without importing every implementation."""
    assert hasattr(ExecutionWorld, "_is_runtime_protocol")
