"""/inspect — show details of one registered capability (CONTROL, read-only)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import get_manager, KNOWN_TYPES


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

        mgr = get_manager(ctype)
        if mgr is None:
            return self.fail(f"Unknown type '{ctype}'. Known: {KNOWN_TYPES}")
        info = await mgr.get_info(name)
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
