"""OptimizeAgent — improves an existing component of whichever type it is given."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.extension import EVOLVABLE_MODULES
from agentevolver.hook.promotion import register_generated
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class OptimizeAgent(Agent):
    """Improves one existing component from execution evidence, then re-registers it.

    The target is named by ``target_name`` and typed by ``target_type``. The agent reads
    the current version, rewrites it under ``extension/``, and reports that path in its
    done_tool reasoning — the same contract as generating, because the same hook installs
    the result.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="optimize_agent")
    description: str = Field(
        default="Improves an existing component — " + ", ".join(EVOLVABLE_MODULES) + " — "
                "from execution evidence. Pass `target_type` to say which kind."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False)

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None,
                 max_step: int = 30, **kwargs: Any):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "optimize_agent",
                         max_step=max_step, **kwargs)

    async def _finalize_run(self, response, ctx):
        return await register_generated(response, ctx, self.model_name, verb="Re-registration")
