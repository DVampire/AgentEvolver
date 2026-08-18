"""/evolve — dispatch an optimize agent to evolve a capability (SKILL).

Example of a SKILL-type command: unlike CONTROL commands it *does* go through the model
— it packages a task and hands it to ``optimize_agent``, telling it the type. A human
shortcut for the evolution workflow the framework already provides.
"""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import SkillCommand, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import AGENT_BACKED_TYPES


@COMMAND.register_module(force=True)
class EvolveCommand(SkillCommand):
    name: str = "evolve"
    description: str = "Dispatch the matching optimize agent to evolve a capability toward a goal."
    usage: str = "/evolve <type> <name> <goal...>"
    permission_mode: str = "workspace_write"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 3:
            return self.fail(f"usage: {self.usage}")
        ctype, name = args[0], args[1]
        goal = " ".join(args[2:])
        if ctype not in AGENT_BACKED_TYPES:
            return self.fail(f"Can't evolve type '{ctype}'. Options: {AGENT_BACKED_TYPES}")

        self.target_agent = "optimize_agent"
        task = f"Optimize the {ctype} '{name}'. Goal: {goal}"
        return await self.dispatch_agent(task, ctx, target_type=ctype, target_name=name)
