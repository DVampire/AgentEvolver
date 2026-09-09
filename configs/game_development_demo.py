"""Solo GameBuilder; native Docker/MCP play integration is still pending."""

from mmengine.config import read_base

with read_base():
    from .agents.game_builder_agent import game_builder_agent
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.adoption import adoption_tool
    from .tools.apply_patch import apply_patch_tool
    from .tools.bash import bash_tool

tag = "game_development_demo"
log_path = "agent.log"
extension_root = "output/game_development_demo/extension"
model_name = "llm_hub/gpt-6-astra"
model_roles = dict(main=model_name, judge=model_name, summarize=model_name)
agent_model_policy = "per_agent"
agent_names = ["game_builder_agent"]
memory_names = ["file_system_memory"]
tool_names = ["bash_tool", "apply_patch_tool", "inspect_tool", "done_tool", "adoption_tool"]
skill_names = ["godot_game_development_skill", "self_evolving_skill"]
connector_names = []
plugin_names = []
workflow_names = []
env_names = ["godot_environment"]

# No engine launch at environment initialization. Configure a local Godot editor
# executable here or via GODOT_BIN; export templates must match its exact version.
godot_environment = dict(binary_path="", max_command_seconds=300, enable_evolving=False)
# Current adapter supports local CLI checks only. Shared Docker mounts and native
# screenshots/input are pending; visual-play acceptance must remain unverified.
# Bash commands must stay in the foreground; Godot will own game process lifetime.

bash_tool.update(enable_evolving=False)
apply_patch_tool.update(enable_evolving=False)
adoption_tool.update(enable_evolving=False)
file_system_memory.update(
    base_dir="memory/file_system", model_name=model_name, enable_evolving=False,
    record_detail_max=2500, recent_fetch=6, working_fetch=8,
)
game_builder_agent.update(
    model_name=model_name,
    env_names=["godot_environment"],
    capability_allowlists={"environment": ["godot_environment"], "agent": []},
    include_agents=False, max_actions=3,
    allow_token_budget_override=False, retain_recent_steps=4,
    fold_at_pressure=0.85,
)
