"""/inspect — show details of one registered capability (CONTROL, read-only)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import call, get_manager, types_supporting


@COMMAND.register_module(force=True)
class InspectCommand(Command):
    name: str = "inspect"
    description: str = "Show details (version, description, path, flags) of one capability."
    type: CommandType = CommandType.CONTROL
    usage: str = "/inspect <type> <name>"
    permission_mode: str = "read_only"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 2:
            return self.fail(f"usage: {self.usage}")
        ctype, name = args[0], args[1]

        # What this command actually needs, rather than a hand-kept list of types. The
        # list was short by `workflow`, `memory` and `plugin` — components the framework
        # generates and registers — so this answered "Unknown type" for all three.
        eligible = types_supporting("get_info")
        if ctype not in eligible:
            return self.fail(
                f"inspect is not available for '{ctype}'. Available: {', '.join(eligible)}")
        mgr = get_manager(ctype)
        info = await call(mgr, "get_info", name)
        if info is None:
            return self.fail(f"{ctype}/{name} not found.")

        lines = [f"{ctype}/{name}"]
        for field in ("version", "description", "enable_evolving", "permission_mode", "path"):
            val = getattr(info, field, None)
            if val is not None and val != "":
                lines.append(f"  {field}: {val}")
        # Prompts carry templates rather than a callable — show their sizes.
        for tf in ("system_template", "user_template"):
            val = getattr(info, tf, None)
            if val is not None:
                lines.append(f"  {tf}: {len(val)} chars")
        return self.ok("\n".join(lines))
