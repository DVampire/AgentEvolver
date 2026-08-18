"""/evaluate — dispatch an evaluate agent to score a capability (SKILL)."""
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import SkillCommand, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import AGENT_BACKED_TYPES


@COMMAND.register_module(force=True)
class EvaluateCommand(SkillCommand):
    name: str = "evaluate"
    description: str = "Dispatch the matching evaluate agent to score a capability."
    usage: str = "/evaluate <type> <name>"
    permission_mode: str = "workspace_write"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if len(args) < 2:
            return self.fail(f"usage: {self.usage}")
        ctype, name = args[0], args[1]
        if ctype not in AGENT_BACKED_TYPES:
            return self.fail(f"Can't evaluate type '{ctype}'. Options: {AGENT_BACKED_TYPES}")

        self.target_agent = "evaluate_agent"
        return await self.dispatch_agent(f"Evaluate the {ctype} '{name}'.", ctx,
                                         target_type=ctype, target_name=name)
