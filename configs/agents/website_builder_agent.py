"""Instance configuration for the dedicated WebsiteBuilderAgent."""

website_builder_agent = dict(
    name="website_builder_agent",
    type="Agent",
    description=(
        "An evolvable website product engineer that builds and deploys releases, "
        "coordinates persistent user co-designers, turns preferences into personalized or "
        "shared changes, and adopts or rolls back new capabilities."
    ),
    model_name="llm_hub/claude-opus-5",
    prompt_name="website_builder_agent",
    memory_name="file_system_memory",
    max_step=180,
    max_token=5000000,
    timeout=14400,
    enable_evolving=True,
    use_memory=True,
)
