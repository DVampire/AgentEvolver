"""Instance configuration for the dedicated WebsiteBuilderAgent."""

website_builder_agent = dict(
    name="website_builder_agent",
    type="Agent",
    description=(
        "An evolvable website product engineer that designs, implements, tests, deploys, "
        "and improves web products from task-defined requirements."
    ),
    model_name="llm_hub/claude-opus-5",
    prompt_name="website_builder_agent",
    memory_name="file_system_memory",
    max_step=180,
    max_token=3000000,
    compact_after_steps=0,
    compact_body_tokens=100000,
    timeout=14400,
    enable_evolving=True,
    use_memory=True,
)
