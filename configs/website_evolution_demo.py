"""Configuration for the participatory website self-evolution demonstration.

The Website Builder inherits MetaAgent's orchestration mechanics with a purpose-built prompt.
One WebsiteUserAgent template is deep-copied into three independent continuable participants,
browser sessions, memories, and dispatcher-scoped scratch workspaces.
"""

from mmengine.config import read_base

with read_base():
    from .agents.browser_agent import browser_agent
    from .agents.website_builder_agent import website_builder_agent
    from .agents.website_user_agents import website_user_agent
    from .base import max_tokens, memory_config, window_size  # noqa: F401
    from .memory.file_system_memory import file_system_memory
    from .tools.adoption import adoption_tool
    from .tools.apply_patch import apply_patch_tool
    from .tools.bash import bash_tool
    from .tools.deploy import deploy_tool
    from .tools.send_message import send_message_tool


tag = "website_evolution_demo"
log_path = "agent.log"
# Product iteration budget, not a quota of framework capability changes. Evolution is
# selected by the shared system policy from execution evidence, never by this count.
optimization_cycles = 5
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
# Every participant needs vision for browser screenshots. Keep three model families;
# Gemini's LLM Hub route was verified with images and tool-result replay.
website_user_models = [
    "llm_hub/gpt-6-astra",
    "llm_hub/claude-fable-5-1",
    "llm_hub/gemini-3.8-flash",
]

memory_names = ["file_system_memory"]

agent_names = [
    "website_builder_agent",
    "browser_agent",
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
#: budget` rather than landing its own work. Steps, wall-clock time, and cumulative
#: token consumption are configured separately below.
BUILDER_MAX_STEP = 320
WALL_CLOCK = 28800
# Cumulative input (including cache) + output per assignment, not context size or
# per-response output. Both role budgets are intentionally generous; the runtime
# also shares the root Builder budget across its descendants.
WORKER_MAX_TOKEN = 100_000_000
BUILDER_MAX_TOKEN = 100_000_000

# Every role uses the shared context assembler. Routes with native compaction use it;
# others use a portable checkpoint. Full input (including cache and tools)
# triggers at 50k, independently of the model window and cumulative execution budget.
_AGENT_CORE = dict(
    # Honor the operator's role budget even when a Builder proposes a smaller child cap.
    allow_token_budget_override=False,
    memory_name=memory_names[0],
    enable_evolving=False,
    use_memory=True,
    retain_recent_steps=4,
    # Full input is calibrated from provider receipts; body-only estimates miss large
    # prefixes. Keep the capacity guard for routes with smaller context windows.
    compact_after_steps=0,
    compact_body_tokens=0,
    compact_input_tokens=50000,
    fold_at_pressure=0.85,
)

_EVOLUTION_WORKER = {
    **_AGENT_CORE,
    "model_name": model_name,
    "max_step": EVOLUTION_MAX_STEP,
    "timeout": WALL_CLOCK,
    "max_token": WORKER_MAX_TOKEN,
}

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
    #
    # 80 then broke on echo_ark (2026-09-05, session e3279065). A 3D game charges steps
    # for locomotion, not just decisions: sailing to a landmark is a dozen keypress +
    # screenshot pairs before any judgement happens. P03 spent all 80 on turn 1 and
    # reported nothing; the round only produced feedback because turn 2 ran again.
    # Canvas products need roughly double a form product's budget for the same verdict.
    "max_step": 140,
    "timeout": 1800,
    "max_token": WORKER_MAX_TOKEN,
    "max_actions": 3,
    "max_screenshots": 2,
}
website_user_agent.update(**_USER)

# Independent release acceptance is stateless and bounded. It validates the exact deployed
# artifact; it does not inherit a participant persona or participate in co-design.
browser_agent.update(
    **{
        **_AGENT_CORE,
        # gemini-3.8-flash's route caps input near 95k, and acceptance is the longest
        # single dispatch in the demo: on orbital_simulator (2026-09-08, session
        # c7556070) its history reached capacity at step ~60 of 90 and every remaining
        # step overflowed, so the verifier could not be called and the release it was
        # judging was recorded as rejected. A larger input window is the difference
        # between a verdict and a silent refusal.
        "model_name": "llm_hub/gpt-6-astra",
        "prompt_name": "browser_agent",
        "env_name": "browser_environment",
        "use_memory": False,
        # Acceptance is a checklist against one deployed artifact, not an open-ended
        # session, but 20 steps is under what a page with 27 interactive elements takes
        # to verify. Still bounded well below a participant's.
        #
        # 45 was exhausted twice on echo_ark (2026-09-05, session e3279065) before the
        # checklist finished, costing a whole acceptance turn each time. A canvas
        # product makes every checklist item cost several steps — reaching salvage to
        # test pickup is a voyage, not a click — so the floor scales with the product,
        # not the element count.
        "max_step": 90,
        "timeout": 1200,
        "max_token": WORKER_MAX_TOKEN,
        "max_actions": 3,
        "max_screenshots": 2,
    }
)

website_builder_agent.update(
    **{
        **_AGENT_CORE,
        "model_name": "llm_hub/claude-fable-5-1",
        "prompt_name": "website_builder_agent",
        "enable_evolving": True,
        "max_step": BUILDER_MAX_STEP,
        "timeout": WALL_CLOCK,
        "max_token": BUILDER_MAX_TOKEN,
    }
)
