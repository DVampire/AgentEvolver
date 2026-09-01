"""Three independently instantiated browser co-designers for the website demo."""

_COMMON = dict(
    type="Agent",
    description=(
        "A browser-only user co-designer that follows one assigned persona, attempts "
        "realistic goals, personalizes the experience, and submits a design contribution."
    ),
    prompt_name="website_user_agent",
    memory_name="file_system_memory",
    env_name="browser_environment",
    max_actions=3,
    max_step=45,
    max_token=1000000,
    timeout=2700,
    max_screenshots=2,
    review_steps=5,
    log_max_length=1000,
    enable_evolving=False,
    use_memory=False,
    subscription_topics=["website.releases"],
)

# Config keys follow ``inflection.underscore(<class name>)``, which is how
# AgentContextManager binds registered classes to their instance configuration.  Runtime
# names retain the more readable numbered form used by prompts and delegation calls.
website_user1_agent = dict(
    name="website_user_1_agent",
    model_name="llm_hub/claude-opus-5",
    **_COMMON,
)
website_user2_agent = dict(
    name="website_user_2_agent",
    model_name="llm_hub/gpt-5.6-sol",
    **_COMMON,
)
website_user3_agent = dict(
    name="website_user_3_agent",
    model_name="llm_hub/deepseek-v4-flash",
    **_COMMON,
)
