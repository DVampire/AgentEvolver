"""/rollback — restore one capability to a previous version (CONTROL, danger).

The deterministic undo. Delegates to the matching manager's ``restore`` — the same
mechanism the framework already uses for versioned components.
"""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import call, get_manager, types_supporting


@COMMAND.register_module(force=True)
class RollbackCommand(Command):
    name: str = "rollback"
    description: str = "Restore a capability to a previous version (deterministic undo)."
    type: CommandType = CommandType.CONTROL
    usage: str = "/rollback <type> <name> <version>"
    permission_mode: str = "danger_full_access"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 3:
            return self.fail(f"usage: {self.usage}")
        ctype, name, version = args[0], args[1], args[2]

        # What this command actually needs, rather than a hand-kept list of types. The
        # list was short by `workflow`, `memory` and `plugin` — components the framework
        # generates and registers — so this answered "Unknown type" for all three.
        eligible = types_supporting("restore")
        if ctype not in eligible:
            return self.fail(
                f"rollback is not available for '{ctype}'. Available: {', '.join(eligible)}")
        mgr = get_manager(ctype)

        result = await call(mgr, "restore", name, version)
        if result is None:
            return self.fail(f"Version {version} not found for {ctype}/{name}.")
        return self.ok(f"Rolled back {ctype}/{name} → {version}.",
                       data={"type": ctype, "name": name, "version": version})
