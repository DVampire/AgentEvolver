"""/deprecate — mark a capability version as deprecated (CONTROL)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import known_types


@COMMAND.register_module(force=True)
class DeprecateCommand(Command):
    name: str = "deprecate"
    description: str = "Mark a specific capability version as deprecated."
    type: CommandType = CommandType.CONTROL
    usage: str = "/deprecate <type> <name> <version>"
    permission_mode: str = "workspace_write"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 3:
            return self.fail(f"usage: {self.usage}")
        ctype, name, version = args[0], args[1], args[2]
        # No manager method needed: deprecation is recorded by `version_manager`, which
        # versions every type. So this is the one command whose precondition really is
        # just "is this a type at all".
        if ctype not in known_types():
            return self.fail(f"Unknown type '{ctype}'. Known: {known_types()}")

        from agentevolver.version import version_manager
        await version_manager.deprecate_version(ctype, name, version)
        return self.ok(f"Deprecated {ctype}/{name}@{version}.")
