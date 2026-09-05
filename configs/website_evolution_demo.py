"""Configuration for the participatory website self-evolution demonstration.

The Website Builder inherits MetaAgent's orchestration mechanics with a purpose-built prompt.
One WebsiteUserAgent template is deep-copied into three independent continuable participants,
browser sessions, memories, and dispatcher-scoped scratch workspaces.
"""

from mmengine.config import read_base

with read_base():
    from .agents.browser_agent import browser_agent
    from .agents.evaluate_agent import evaluate_agent
    from .agents.generate_agent import generate_agent
    from .agents.optimize_agent import optimize_agent
    from .agents.website_builder_agent import website_builder_agent
    from .agents.website_user_agents import website_user_agent
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.apply_patch import apply_patch_tool
    from .tools.bash import bash_tool
    from .tools.deploy import deploy_tool
    from .tools.adoption import adoption_tool
    from .tools.send_message import send_message_tool


tag = "website_evolution_demo"
log_path = "agent.log"
# Product iteration budget, not a quota of framework capability changes. Evolution is
# selected by the shared system policy from execution evidence, never by this count.
optimization_cycles = 5
initial_step_budget = 36
iteration_step_budget = 30
# Keep this demonstration's generated/optimized components isolated from the global
# extension library.  Besides making rollback auditable, this avoids depending on a
# machine-wide manifest that may belong to another OS user.
extension_root = "output/website_evolution_demo/extension"

# The global model remains the default for memory and support workers.  This demo deliberately
# preserves role-specific agent models so independent co-designers do not simulate three users
# with the same model family.
model_name = "llm_hub/claude-opus-5"
model_roles = dict(
    main=model_name,
    judge=model_name,
    summarize=model_name,
)
agent_model_policy = "per_agent"
# Every participant needs vision for browser screenshots. DeepSeek's experimental
# visual route is distinct from the text-only Flash 0731 alias.
website_user_models = [
    "llm_hub/gpt-6-astra",
    "llm_hub/claude-fable-5-1",
    "llm_hub/deepseek-v4-flash-vision-exp",
]

memory_names = ["file_system_memory"]

agent_names = [
    "website_builder_agent",
    "browser_agent",
    "generate_agent",
    "optimize_agent",
    "evaluate_agent",
    "website_user_agent",
]

# Bash owns workspace inspection, search, Git, build, and tests; inspect resolves one
# registered capability contract on demand. Apply_patch is the only source mutation primitive.
tool_names = [
    "bash_tool",
    "apply_patch_tool",
    "inspect_tool",
    "deploy_tool",
    "done_tool",
    "send_message_tool",
    "adoption_tool",
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
apply_patch_tool.update(enable_evolving=False)
deploy_tool.update(enable_evolving=False)
adoption_tool.update(enable_evolving=False)
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
#: The builder's budget. 180 was not enough: a measured run spent 171 steps and was cut
#: off still producing ~3k output tokens a step, so it ended `FAILED: Reached the step
#: budget` rather than landing its own work. The cost of raising it is bounded by
#: `max_token` and `timeout`, which stop a runaway long before the step count does; the
#: cost of leaving it low is a run that throws away everything it could not persist.
BUILDER_MAX_STEP = 320
WALL_CLOCK = 28800
MAX_TOKEN = 10000000

# Every role uses the same cache-aware context policy as the SWE-bench MetaAgent.  Native
# compaction is selected by the configured memory model when supported and otherwise falls
# back to a portable text checkpoint, so a heterogeneous user panel is not a reason to turn
# compaction off globally. The Builder uses GPT-6; support workers retain Opus 5,
# while the co-design panel spans three model families.
_AGENT_CORE = dict(
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    retain_recent_steps=4,
    compact_after_steps=18,
    # Fold at 60k rather than 100k. Cache reads are charged per request, so a working
    # context that sits at 80-120k is paid for on every step that follows: measured over
    # 171 builder steps, the 57 steps spent above 80k cost $2.48 in reads alone against
    # $1.89 for the 82 steps between 40k and 80k. Folding sooner trades a few more
    # checkpoint calls — 8% of the cache-write bill, $0.79 across the whole run — for a
    # smaller prefix on every step after each fold.
    compact_body_tokens=60000,
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
    "model_name": website_user_models[0],
    "prompt_name": "website_user_agent",
    "env_name": "browser_environment",
    # Continuable participants live across the initial co-design turn and later iterations.
    # They need the same durable checkpoint + exact-tail
    # protocol as every other long-running Agent, especially after browser state resets.
    "use_memory": True,
    # 30 was not a session, it was an interrupted one. Measured on a single participant
    # against the deployed site: all 30 steps were real interaction — one `goto`, six
    # page reads, nineteen clicks, three inputs, a scroll — and the budget ran out
    # before `done_tool`, so the round produced no report at all. A browser round costs
    # roughly one step per interaction, and a resident participant pays it again on
    # every release.
    "max_step": 80,
    "timeout": 1800,
    "max_token": 1000000,
    "max_actions": 3,
    "max_screenshots": 2,
}
website_user_agent.update(**_USER)

# Independent release acceptance is stateless and bounded. It validates the exact deployed
# artifact; it does not inherit a participant persona or participate in co-design.
browser_agent.update(
    **{
        **_AGENT_CORE,
        "model_name": "llm_hub/gpt-5.6-sol",
        "prompt_name": "browser_agent",
        "env_name": "browser_environment",
        "use_memory": False,
        # Acceptance is a checklist against one deployed artifact, not an open-ended
        # session, but 20 steps is under what a page with 27 interactive elements takes
        # to verify. Still bounded well below a participant's.
        "max_step": 45,
        "timeout": 1200,
        "max_token": 500000,
        "max_actions": 3,
        "max_screenshots": 2,
    }
)

website_builder_agent.update(
    **{
        **_AGENT_CORE,
        "model_name": "llm_hub/gpt-6-astra",
        "prompt_name": "website_builder_agent",
        "enable_evolving": True,
        "max_step": BUILDER_MAX_STEP,
        "timeout": WALL_CLOCK,
        "max_token": MAX_TOKEN,
        "initial_step_budget": initial_step_budget,
        "iteration_step_budget": iteration_step_budget,
    }
)
