"""One Website Builder builds, browses, critiques and evolves its own methods."""
from mmengine.config import read_base

with read_base():
    from .agents.website_builder_agent import website_builder_agent
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.adoption import adoption_tool
    from .tools.apply_patch import apply_patch_tool
    from .tools.bash import bash_tool
    from .tools.deploy import deploy_tool

tag = "website_evolution_demo"
log_path = "agent.log"
optimization_cycles = 5
extension_root = "output/website_evolution_demo/extension"
model_name = "llm_hub/gpt-6-astra"
model_roles = dict(main=model_name, judge=model_name, summarize=model_name)
agent_model_policy = "per_agent"
agent_names = ["website_builder_agent"]
memory_names = ["file_system_memory"]
tool_names = [
    "bash_tool", "apply_patch_tool", "inspect_tool", "deploy_tool", "done_tool", "adoption_tool",
]
skill_names = ["frontend_ui_engineering_skill", "webapp_testing_skill", "self_evolving_skill"]
connector_names = []
plugin_names = []
workflow_names = []
env_names = ["job", "browser_environment"]
browser_environment = dict(
    base_dir="environment/browser", headless=True,
    # Preserve clean visual evidence; element coordinates remain in textual state.
    viewport=dict(width=1280, height=900), use_sandbox=False, use_som=False,
    state_detail="elements", max_state_elements=140, command_timeout=30.0,
)

bash_tool.update(enable_evolving=False)
apply_patch_tool.update(enable_evolving=False)
deploy_tool.update(enable_evolving=False)
adoption_tool.update(enable_evolving=False)
file_system_memory.update(
    base_dir="memory/file_system", model_name=model_name, enable_evolving=False,
    record_detail_max=2500, recent_fetch=6, working_fetch=8,
)

# Browsing now consumes this same agent's steps. Keep room for six actual visits,
# implementation, capability comparisons, adoption and subsequent consumer use.
website_builder_agent.update(
    model_name=model_name, enable_evolving=True, include_agents=False,
    env_names=["job", "browser_environment"],
    capability_allowlists={"environment": ["job", "browser_environment"], "agent": []},
    max_step=10_000, timeout=28800, max_token=1_000_000_000, max_actions=3, max_screenshots=1,
    allow_token_budget_override=False, memory_name="file_system_memory", use_memory=True,
    use_plan=True, compact_strategy="text",
    retain_recent_steps=4, compact_after_steps=0, compact_body_tokens=0,
    compact_input_tokens=100_000, fold_at_pressure=0.85,
)
