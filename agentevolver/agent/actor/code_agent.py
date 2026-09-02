"""CodeAgent — reads, edits, builds and tests code."""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class CodeAgent(Agent):
    """A code agent, and nothing but a declaration.

    Its whole difference from any other agent is a name, a prompt and a step budget. That
    is what an actor should be: the loop, the context assembler and the executor are the
    same for every agent, so an actor that overrides one of them is either a genuine new
    behaviour or an accident. This one is neither.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="code_agent")
    description: str = Field(
        default="A code agent that reads, writes, and edits source code files, "
        "runs tests, and commits changes using git."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="code_agent")
    max_step: int = Field(default=30)
    enable_evolving: bool = Field(default=False)


__all__ = ["CodeAgent"]
