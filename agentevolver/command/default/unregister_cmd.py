"""/unregister — remove a capability from the registry (CONTROL, danger)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import call, get_manager, types_supporting


@COMMAND.register_module(force=True)
class UnregisterCommand(Command):
    name: str = "unregister"
    description: str = "Remove a capability from the active registry."
    type: CommandType = CommandType.CONTROL
    usage: str = "/unregister <type> <name>"
    permission_mode: str = "danger_full_access"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 2:
            return self.fail(f"usage: {self.usage}")
        ctype, name = args[0], args[1]

        # What this command actually needs, rather than a hand-kept list of types. The
        # list was short by `workflow`, `memory` and `plugin` — components the framework
        # generates and registers — so this answered "Unknown type" for all three.
        eligible = types_supporting("unregister")
        if ctype not in eligible:
            return self.fail(
                f"unregister is not available for '{ctype}'. Available: {', '.join(eligible)}")
        mgr = get_manager(ctype)

        ok = await call(mgr, "unregister", name)
        if ok:
            return self.ok(f"Unregistered {ctype}/{name}.")
        return self.fail(f"{ctype}/{name} not found.")
