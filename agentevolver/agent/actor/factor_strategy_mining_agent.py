"""One researcher owns data acquisition, factor discovery and strategy research."""

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class FactorStrategyMiningAgent(MetaAgent):
    name: str = "factor_strategy_mining_agent"
    description: str = (
        "Develop reusable market-data and backtesting capabilities, discover causal "
        "factors, research strategies and publish reproducible interactive reports."
    )
    prompt_name: str = "factor_strategy_mining_agent"
    include_agents: bool = False
    enable_evolving: bool = True
    use_plan: bool = True
    env_names: list[str] = Field(default_factory=lambda: ["job", "browser_environment"])
    capability_allowlists: dict[str, list[str]] = Field(default_factory=lambda: {
        "environment": ["job", "browser_environment"], "agent": [],
    })
    accepts_evolved: list[str] = Field(default_factory=lambda: ["environment"])


__all__ = ["FactorStrategyMiningAgent"]
