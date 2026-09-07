"""Factor discovery specialist using the standard native-capability agent loop."""

from pydantic import Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class FactorMiningAgent(Agent):
    name: str = "factor_mining_agent"
    description: str = "Discover diverse causal factors, test their evidence, and submit promising candidates for independent admission."
    prompt_name: str = "factor_mining_agent"
    max_step: int = 40
    max_token: int = 100_000_000
    use_memory: bool = False
    use_plan: bool = False
    include_agents: bool = False
    env_names: list[str] = Field(default_factory=lambda: ["factor_mining"])
    capability_allowlists: dict[str, list[str]] = Field(default_factory=lambda: {
        "tool": ["done_tool"], "environment": ["factor_mining"], "agent": [],
        "skill": [], "connector": [], "plugin": [], "workflow": [],
    })
    accepts_evolved: list[str] = Field(default_factory=list)
