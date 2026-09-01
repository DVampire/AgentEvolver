"""Configuration for the participatory website self-evolution demonstration.

The Website Builder inherits MetaAgent's orchestration mechanics with a purpose-built prompt.
One WebsiteUserAgent template is deep-copied into three independent runtime subscriptions,
browser sessions, memories, and dispatcher-scoped scratch workspaces.
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
    from .agents.website_user_agents import website_user_agent
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
# V0 is the blind baseline; these are five material optimization transitions
# ending at V5.  The launcher repeats this value in the runtime manifest and
# validation rejects drift between the config and task contract.
optimization_cycles = 5
# Keep this demonstration's generated/optimized components isolated from the global
# extension library.  Besides making rollback auditable, this avoids depending on a
# machine-wide manifest that may belong to another OS user.
extension_root = "output/website_evolution_demo/extension"

# The global model remains the default for memory and support workers.  This demo deliberately
# preserves role-specific agent models so independent co-designers do not simulate three users
# with the same model family.
model_name = "llm_hub/claude-opus-5"
agent_model_policy = "per_agent"
website_user_models = [
    "llm_hub/claude-opus-5",
    "llm_hub/gpt-5.6-sol",
    "llm_hub/deepseek-v4-flash",
]

memory_names = ["file_system_memory"]

agent_names = [
    "website_builder_agent",
    "code_agent",
    "general_agent",
    "reviewer_agent",
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
    "website_user_agent",
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
# separate page/context per child session; dispatcher deep copies prevent instance fields from
# racing while those sessions run concurrently.
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
    # Keep the same cache-aware memory window used by the SWE-bench MetaAgent.
    # The website roles differ in their task and model route, not in the context
    # protocol that bounds exact history and carries durable checkpoints.
    record_detail_max=2500,
    recent_fetch=6,
    working_fetch=8,
)


WORKER_MAX_STEP = 60
EVOLUTION_MAX_STEP = 60
BUILDER_MAX_STEP = 180
WALL_CLOCK = 28800
MAX_TOKEN = 10000000

# Every role uses the same cache-aware context policy as the SWE-bench MetaAgent.  Native
# compaction is selected by the configured memory model when supported and otherwise falls
# back to a portable text checkpoint, so a heterogeneous user panel is not a reason to turn
# compaction off globally. Models are assigned below by role: Builder/Code use Opus 5, while
# the co-design panel spans three model families.
_AGENT_CORE = dict(
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    retain_recent_steps=4,
    compact_after_steps=18,
    compact_body_tokens=100000,
    fold_at_pressure=0.85,
)

_WORKER = {
    **_AGENT_CORE,
    "model_name": model_name,
    "max_step": WORKER_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": MAX_TOKEN,
}
code_agent.update(**_WORKER)
general_agent.update(**_WORKER)
reviewer_agent.update(**_WORKER)

_EVOLUTION_WORKER = {
    **_AGENT_CORE,
    "model_name": model_name,
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
    # Subscribers live across V0→V5. They need the same durable checkpoint + exact-tail
    # protocol as every other long-running Agent, especially after browser state resets.
    "use_memory": True,
    "max_step": 30,
    "timeout": 1800,
    "max_token": 1000000,
    "max_actions": 3,
    "max_screenshots": 2,
    "subscription_topics": ["website.releases"],
}
website_user_agent.update(**_USER)

website_builder_agent.update(**{
    **_AGENT_CORE,
    "model_name": "llm_hub/claude-opus-5",
    "prompt_name": "website_builder_agent",
    "enable_evolving": True,
    "max_step": BUILDER_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": MAX_TOKEN,
})
