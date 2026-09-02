"""GeneralAgent — iterative reasoning and action over tools, skills and connectors."""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class GeneralAgent(Agent):
    """The default worker. Declaration only."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="general_agent")
    description: str = Field(
        default="An iterative agent that reasons and acts by using tools, skills, and direct "
        "responses to accomplish tasks accurately, safely, and efficiently."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="general_agent")
    max_step: int = Field(default=20)
    enable_evolving: bool = Field(default=False)


__all__ = ["GeneralAgent"]
