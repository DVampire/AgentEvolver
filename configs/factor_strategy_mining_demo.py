"""One researcher builds its data connector and two backtesting environments."""
from mmengine.config import read_base

with read_base():
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.adoption import adoption_tool
    from .tools.apply_patch import apply_patch_tool
    from .tools.bash import bash_tool
    from .tools.deploy import deploy_tool

tag = "factor_strategy_mining_demo"
log_path = "agent.log"
extension_root = "output/factor_strategy_mining_demo/extension"
model_name = "llm_hub/gpt-6-astra"
model_roles = dict(main=model_name, judge=model_name, summarize=model_name)
agent_model_policy = "per_agent"
agent_names = ["factor_strategy_mining_agent"]
memory_names = ["file_system_memory"]
tool_names = ["bash_tool", "apply_patch_tool", "inspect_tool", "deploy_tool", "done_tool", "adoption_tool"]
skill_names = ["factor_strategy_research_skill", "frontend_ui_engineering_skill",
               "webapp_testing_skill", "self_evolving_skill"]
connector_names = []
plugin_names = []
workflow_names = []
benchmark_names = []
# Runtime experiment policy belongs to the assembly, not the product task folder.
# The shared launcher applies these defaults after loading the task document.
task_manifest_defaults = dict(
    subscribers=[],
    deployment=dict(required_releases=2, topic="deployment.ready"),
    run_policy=dict(self_review=True),
    evolution=dict(require_verified_improvement=True,
                   required_module_counts=dict(connector=1, environment=2)),
    research=dict(holdout_control="protocol_only"),
)
env_names = ["job", "browser_environment"]
browser_environment = dict(
    base_dir="environment/browser", headless=True, use_sandbox=False, use_som=False,
    viewport=dict(width=1440, height=1000), state_detail="elements",
    max_state_elements=140, command_timeout=30.0,
)

bash_tool.update(enable_evolving=False)
apply_patch_tool.update(enable_evolving=False)
deploy_tool.update(enable_evolving=False)
adoption_tool.update(enable_evolving=False)
file_system_memory.update(
    base_dir="memory/file_system", model_name=model_name, enable_evolving=False,
    record_detail_max=2500, recent_fetch=6, working_fetch=8,
)
factor_strategy_mining_agent = dict(
    name="factor_strategy_mining_agent", type="Agent",
    prompt_name="factor_strategy_mining_agent", model_name=model_name,
    include_agents=False, use_plan=True, use_memory=True, enable_evolving=True,
    memory_name="file_system_memory", env_names=env_names,
    capability_allowlists={"environment": env_names, "agent": []},
    accepts_evolved=["environment"],
    max_step=10_000, max_token=1_000_000_000, timeout=28800,
    max_actions=3, max_screenshots=1, allow_token_budget_override=False,
    compact_strategy="text", compact_after_steps=0, compact_body_tokens=0,
    compact_input_tokens=100_000, retain_recent_steps=4, fold_at_pressure=0.85,
)
