"""Strategy research specialist; all execution remains in the common Agent loop."""

from agentevolver.agent.actor.factor_mining_agent import FactorMiningAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class StrategyMiningAgent(FactorMiningAgent):
    name: str = "strategy_mining_agent"
    description: str = "Combine independently admitted factors into causal strategies, validate them and diagnose missing signals."
    prompt_name: str = "strategy_mining_agent"
