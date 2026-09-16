"""A minimal concrete agent, for tests that need one and do not care which.

Borrowing whichever actor was convenient — `GeneralAgent`, `CodeAgent` — quietly coupled
a test to that actor's configuration, so a change to the actor broke a test whose subject
had not moved. This class exists to be an ordinary `Agent` and nothing else.
"""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent


class AgentProbe(Agent):
    """Concrete, inert, and deliberately featureless."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="agent_probe")
    description: str = Field(default="A concrete agent, for tests only.")
    metadata: Dict[str, Any] = Field(default={})
    enable_evolving: bool = Field(default=False)


__all__ = ["AgentProbe"]
