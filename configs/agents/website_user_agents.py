"""Instance configuration for the reusable WebsiteUserAgent template."""

website_user_agent = dict(
    name="website_user_agent",
    type="Agent",
    description=(
        "A browser-only user or co-designer that evaluates a website from assigned "
        "user context and reports grounded experience evidence through the UI."
    ),
    model_name="llm_hub/claude-opus-5",
    prompt_name="website_user_agent",
    memory_name="file_system_memory",
    env_name="browser_environment",
    max_actions=3,
    max_step=30,
    max_token=1000000,
    timeout=1800,
    max_screenshots=2,
    review_steps=4,
    log_max_length=1000,
    enable_evolving=False,
    use_memory=True,
)
