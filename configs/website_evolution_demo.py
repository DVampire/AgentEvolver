"""Configuration for the participatory website self-evolution demonstration.

The Website Builder inherits MetaAgent's orchestration mechanics with a purpose-built prompt.
One WebsiteUserAgent template is deep-copied into three independent runtime subscriptions,
browser sessions, memories, and dispatcher-scoped scratch workspaces.
"""

from mmengine.config import read_base

with read_base():
    from .agents.evaluate_agent import evaluate_agent
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.website_builder_agent import website_builder_agent
    from .agents.website_user_agents import website_user_agent
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.bash import bash_tool
    from .tools.deploy import deploy_tool
    from .tools.escalate import escalate_tool
    from .tools.evolution import evolution_tool
    from .tools.publish_event import publish_event_tool
    from .tools.send_message import send_message_tool
    from .tools.website_release_gate import website_release_gate_tool


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
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
    "website_user_agent",
]

# Bash owns local file, search, Git, build, and test operations. Keep only tools that add a
# distinct runtime protocol; parallel read/write/search wrappers would duplicate the same
# authority and inflate every model request. Browser co-designers further narrow this list.
tool_names = [
    "bash_tool",
    "deploy_tool",
    "escalate_tool",
    "reply_tool",
    "done_tool",
    "publish_event_tool",
    "website_release_gate_tool",
    "send_message_tool",
    "evolution_tool",
]

# Baseline product methods are intentionally useful but not exhaustive.  In particular, no
# localization/RTL methodology is pre-mounted: if real persona evidence exposes that gap, the
# builder must prove and evolve it instead of pretending the capability was present all along.
# The last three skills are required by the dedicated evolution workers themselves.
skill_names = [
    "frontend_ui_engineering_skill",
    "webapp_testing_skill",
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
deploy_tool.update(enable_evolving=False)
evolution_tool.update(enable_evolving=False)
escalate_tool.update(enable_evolving=False)
publish_event_tool.update(enable_evolving=False)
website_release_gate_tool.update(enable_evolving=False, max_release=optimization_cycles)
send_message_tool.update(enable_evolving=False)


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


EVOLUTION_MAX_STEP = 60
BUILDER_MAX_STEP = 180
WALL_CLOCK = 28800
MAX_TOKEN = 10000000

# Every role uses the same cache-aware context policy as the SWE-bench MetaAgent.  Native
# compaction is selected by the configured memory model when supported and otherwise falls
# back to a portable text checkpoint, so a heterogeneous user panel is not a reason to turn
# compaction off globally. The Builder and evolution workers use Opus 5, while the co-design
# panel spans three model families.
_AGENT_CORE = dict(
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    retain_recent_steps=4,
    compact_after_steps=18,
    compact_body_tokens=100000,
    fold_at_pressure=0.85,
)

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
