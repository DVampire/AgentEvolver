"""Factor/strategy research; execution is in examples/run_factor_mining.py."""

tag = "factor_mining"
log_path = "factor_mining.log"
model_name = "llm_hub/claude-opus-5"
agent_names = ["factor_mining_agent", "strategy_mining_agent"]
tool_names = ["done_tool"]
env_names = ["factor_mining"]
memory_names = []
skill_names = []
connector_names = []
benchmark_names = ["factor_mining"]

factor_mining_agent = dict(
    name="factor_mining_agent", type="FactorMiningAgent", prompt_name="factor_mining_agent",
    model_name=model_name, use_memory=False, max_step=40, max_token=100_000_000,
    timeout=1800, enable_evolving=False,
)
strategy_mining_agent = dict(
    name="strategy_mining_agent", type="StrategyMiningAgent", prompt_name="strategy_mining_agent",
    model_name=model_name, use_memory=False, max_step=40, max_token=100_000_000,
    timeout=1800, enable_evolving=False,
)
factor_mining_environment = dict(workspace="")

# Fixed per study. Changing these requires a new output directory, not a retry.
research_protocol = dict(
    horizons=[1, 5], min_samples=64, min_rank_ic=0.02, max_nan_fraction=0.3,
    max_correlation=0.7, min_valid_retention=0.25, min_sharpe=0.5,
    min_annual_return=0.0, max_drawdown=0.4, cost_bps=5, slippage_bps=2,
    periods_per_year=8766, validation_budget=8, max_batch=8,
)
max_rounds = 3
no_progress_rounds = 2
minimum_improvement = 0.05  # worst-asset validation Sharpe, not training return
worker_timeout = 1800
