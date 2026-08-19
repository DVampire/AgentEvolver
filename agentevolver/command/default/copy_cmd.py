"""/copy — copy a capability to a new name/version (CONTROL)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import call, get_manager, types_supporting


@COMMAND.register_module(force=True)
class CopyCommand(Command):
    name: str = "copy"
    description: str = "Copy a capability to a new name (or bump version if no new name)."
    type: CommandType = CommandType.CONTROL
    usage: str = "/copy <type> <name> [new_name]"
    permission_mode: str = "workspace_write"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 2:
            return self.fail(f"usage: {self.usage}")
        ctype, name = args[0], args[1]
        new_name = args[2] if len(args) > 2 else None

        # What this command actually needs, rather than a hand-kept list of types. The
        # list was short by `workflow`, `memory` and `plugin` — components the framework
        # generates and registers — so this answered "Unknown type" for all three.
        eligible = types_supporting("copy")
        if ctype not in eligible:
            return self.fail(
                f"copy is not available for '{ctype}'. Available: {', '.join(eligible)}")
        mgr = get_manager(ctype)

        result = await call(mgr, "copy", name, new_name)
        target = getattr(result, "name", new_name or name)
        version = getattr(result, "version", "?")
        return self.ok(f"Copied {ctype}/{name} → {target}@{version}.",
                       data={"type": ctype, "name": target, "version": version})
