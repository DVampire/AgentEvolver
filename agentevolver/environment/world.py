"""The execution world: the operations an agent performs *somewhere*.

Two complete implementations of the same set already exist and share no contract.
`SSHEnvironment` does `run`, `read`, `write`, `edit`, `list`, `grep`, `glob`, `remove`
against a remote host. The default tools — `bash`, `read_file`, `write_file`, `edit_file`
— do the same things against this one, reaching the host directly through `open()` and
`asyncio.create_subprocess_shell`.

That they agree is currently a coincidence maintained by hand. Nothing relates them, so
an operation added to one side is simply absent from the other, and the absence surfaces
as an agent that can edit a file on a remote host but not glob for one — discovered by a
model, mid-task, as a capability that turns out not to exist.

This module is the contract they both nearly satisfy. It is a `Protocol`, checked
structurally: nothing has to inherit from it, and `tests/test_execution_world.py` reports
where the two worlds have drifted rather than asserting they never will.

**What this is not, yet.** The tools do not route through here. Giving `read_file` a
world to read from — so that selecting a different world moves the agent somewhere else
without touching a tool — is the change this contract makes mechanical, and it is
deliberately not bundled with defining it: the file tools are the most-used code in the
product, and rewiring them is worth doing against a contract that is already agreed.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

#: The operations that make up one execution world.
#:
#: Taken from `SSHEnvironment`, which is the more complete of the two implementations —
#: it is the one that had to be explicit, because nothing about a remote host can be done
#: by reaching for a local primitive.
WORLD_OPERATIONS = ("run", "read", "write", "edit", "list", "grep", "glob", "remove")


@runtime_checkable
class ExecutionWorld(Protocol):
    """Where an agent's file and process work actually happens.

    Structural on purpose. `SSHEnvironment` already satisfies most of this without knowing
    the protocol exists, and requiring it to inherit would mean editing a working class to
    record a fact that is already true.

    Every method returns a dict rather than a typed result because that is what both
    implementations return today; narrowing it is a separate change with its own migration,
    and inventing a shape neither side produces would make this contract aspirational
    rather than descriptive.
    """

    async def run(self, command: str, **kwargs: Any) -> Dict[str, Any]:
        """Execute a command and report its outcome."""

    async def read(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Read a file's contents."""

    async def write(self, path: str, content: str, **kwargs: Any) -> Dict[str, Any]:
        """Replace a file's contents."""

    async def edit(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Change part of a file in place."""

    async def list(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """List a directory."""

    async def grep(self, pattern: str, **kwargs: Any) -> Dict[str, Any]:
        """Search file contents."""

    async def glob(self, pattern: str, **kwargs: Any) -> Dict[str, Any]:
        """Search file names."""

    async def remove(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Delete a path."""


def missing_operations(candidate: Any) -> list:
    """Which world operations ``candidate`` does not provide, in contract order.

    Reported rather than raised: an environment that offers seven of the eight is useful,
    and refusing to load it would trade a partial world for none at all. The list is what
    lets a caller say *which* capability an agent will not have somewhere.
    """
    return [name for name in WORLD_OPERATIONS if not callable(getattr(candidate, name, None))]


__all__ = ["WORLD_OPERATIONS", "ExecutionWorld", "missing_operations"]
