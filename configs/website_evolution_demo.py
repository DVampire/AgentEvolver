"""Configuration for the participatory website self-evolution demonstration.

The Website Builder is the MetaAgent with a purpose-built prompt.  Three concrete
BrowserAgent subclasses provide independent Python instances, browser sessions, and dispatcher-
scoped scratch workspaces so co-design turns cannot leak plans or artifacts across participants.
"""

from mmengine.config import read_base

with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.website_builder_agent import website_builder_agent
    from .agents.code_agent import code_agent
    from .agents.general_agent import general_agent
    from .agents.reviewer_agent import reviewer_agent
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.evaluate_agent import evaluate_agent
    from .agents.website_user_agents import (
        website_user1_agent,
        website_user2_agent,
        website_user3_agent,
    )
    from .tools.bash import bash_tool
    from .tools.read_file import read_file_tool
    from .tools.write_file import write_file_tool
    from .tools.edit_file import edit_file_tool
    from .tools.list_dir import list_dir_tool
    from .tools.grep_search import grep_search_tool
    from .tools.glob_search import glob_search_tool
    from .tools.git import git_tool
    from .tools.read_image import read_image_tool
    from .tools.deploy import deploy_tool
    from .tools.evolution import evolution_tool
    from .tools.ask_user import ask_user_question
    from .tools.escalate import escalate_tool
    from .tools.exit_plan_mode import exit_plan_mode
    from .tools.publish_event import publish_event_tool
    from .memory.file_system_memory import file_system_memory


tag = "website_evolution_demo"
log_path = "agent.log"
# Keep this demonstration's generated/optimized components isolated from the global
# extension library.  Besides making rollback auditable, this avoids depending on a
# machine-wide manifest that may belong to another OS user.
extension_root = "output/website_evolution_demo/extension"

# process_agent() deliberately normalizes configured agents to this global model.  Keep the
# demo explicit and reproducible: the builder and its co-design/evolution workers all use
# this route unless the launcher supplies an explicit `--model` override.
model_name = "llm_hub/claude-opus-5"

memory_names = ["file_system_memory"]

agent_names = [
    "website_builder_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
    "website_user_1_agent",
    "website_user_2_agent",
    "website_user_3_agent",
]

# Resident tools cover the coupled build/deploy/evolution loop. Browser co-designers override
# tool projection and remain pure environment agents.  Connectors/plugins/workflows stay absent
# so the demo does not gain unrelated capabilities or prompt weight.
tool_names = [
    "bash_tool",
    "read_file_tool",
    "write_file_tool",
    "edit_file_tool",
    "list_dir_tool",
    "grep_search_tool",
    "glob_search_tool",
    "git_tool",
    "read_image_tool",
    "deploy_tool",
    "ask_user_question",
    "escalate_tool",
    "reply_tool",
    "report_tool",
    "done_tool",
    "exit_plan_mode",
    "publish_event_tool",
    "evolution_tool",
]

# Baseline product methods are intentionally useful but not exhaustive.  In particular, no
# localization/RTL methodology is pre-mounted: if real persona evidence exposes that gap, the
# builder must prove and evolve it instead of pretending the capability was present all along.
# The last three skills are required by the dedicated evolution workers themselves.
skill_names = [
    "frontend_ui_engineering_skill",
    "api_and_interface_design_skill",
    "webapp_testing_skill",
    "deploy_skill",
    "self_evolving_skill",
    "generate_skill",
    "optimize_skill",
    "evaluate_skill",
]

connector_names = []
plugin_names = []
workflow_names = []

# `job` makes background build processes observable.  One shared BrowserEnvironment creates a
# separate page/context per child session; the three separate Agent classes prevent instance
# fields from racing while those sessions run concurrently.
env_names = ["job", "browser_environment"]

browser_environment = dict(
    base_dir="environment/browser",
    headless=True,
    viewport=dict(width=1280, height=900),
    use_sandbox=False,
    use_som=True,
    state_detail="elements",
    max_state_elements=140,
    command_timeout=30.0,
)


# ---------------- Tool configuration ----------------
bash_tool.update(enable_evolving=False)
read_file_tool.update(enable_evolving=False)
write_file_tool.update(enable_evolving=False)
edit_file_tool.update(enable_evolving=False)
list_dir_tool.update(enable_evolving=False)
grep_search_tool.update(enable_evolving=False)
glob_search_tool.update(enable_evolving=False)
git_tool.update(enable_evolving=False, timeout=60)
read_image_tool.update(enable_evolving=False)
deploy_tool.update(enable_evolving=False)
evolution_tool.update(enable_evolving=False)
ask_user_question.update(enable_evolving=False)
escalate_tool.update(enable_evolving=False)
exit_plan_mode.update(enable_evolving=False)
publish_event_tool.update(enable_evolving=False)


# ---------------- Memory configuration ----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    # Keep the demonstration attributable: feedback may evolve a missing website-building
    # capability, not opportunistically rewrite the runtime's memory implementation.
    enable_evolving=False,
    record_detail_max=3000,
    recent_fetch=8,
    working_fetch=10,
)


WORKER_MAX_STEP = 60
EVOLUTION_MAX_STEP = 60
BUILDER_MAX_STEP = 180
WALL_CLOCK = 14400
MAX_TOKEN = 5000000

# Every role shares the same model, memory identity, and provider-safe context policy.
# Role configs below override only genuine behavioral differences (browser environment,
# memory use, step budget, and whether the Builder may evolve capabilities).
_AGENT_CORE = dict(
    model_name=model_name,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    # llm_hub/deepseek-v4-flash currently returns an empty assistant message for the
    # memory-compaction operation. Keep every role on the same non-compacting policy so
    # a worker does not fail after the Builder successfully delegated to it.
    retain_recent_steps=8,
    compact_after_steps=0,
    compact_body_tokens=0,
    fold_at_pressure=0.0,
)

_WORKER = {
    **_AGENT_CORE,
    "max_step": WORKER_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": MAX_TOKEN,
}
code_agent.update(**_WORKER)
general_agent.update(**_WORKER)
reviewer_agent.update(**_WORKER)

_EVOLUTION_WORKER = {
    **_AGENT_CORE,
    "max_step": EVOLUTION_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": MAX_TOKEN,
}
generate_agent.update(**_EVOLUTION_WORKER)
optimize_agent.update(**_EVOLUTION_WORKER)
evaluate_agent.update(**_EVOLUTION_WORKER)

_USER = {
    **_AGENT_CORE,
    "prompt_name": "website_user_agent",
    "env_name": "browser_environment",
    "use_memory": False,
    "max_step": 50,
    "timeout": 2700,
    "max_token": 1000000,
    "max_actions": 3,
    "max_screenshots": 2,
    "subscription_topics": ["website.releases"],
}
website_user1_agent.update(**_USER)
website_user2_agent.update(**_USER)
website_user3_agent.update(**_USER)

website_builder_agent.update(**{
    **_AGENT_CORE,
    "prompt_name": "website_builder_agent",
    "enable_evolving": True,
    "max_step": BUILDER_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": MAX_TOKEN,
})
