"""GenerateAgent — creates a new component of whichever type it is asked for."""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.extension import EVOLVABLE_MODULES
from agentevolver.hook.promotion import register_generated
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class GenerateAgent(Agent):
    """Creates one new component from a natural-language description.

    Every component type had its own generate agent — eight files whose executable code
    differed by a noun. What actually varies by type lives where it belongs: the creator
    skill says how to write that kind of thing, and the registration hook says how to
    install it. So the type is an input, ``target_type``, not a class.

    Runs the base-class standard loop, then registers what it wrote. The agent writes the
    source under the conventional ``extension/`` path and reports it in its done_tool
    reasoning, which is how the hook finds it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="generate_agent")
    description: str = Field(
        default="Creates a new component — " + ", ".join(EVOLVABLE_MODULES) + " — from a "
                "description. Pass `target_type` to say which kind."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    prompt_name: str = Field(default="generate_agent")
    max_step: int = Field(default=30)
    enable_evolving: bool = Field(default=False)


    async def finalize(self, response):
        return await register_generated(response, self.ctx, self.model_name, verb="Registration")
