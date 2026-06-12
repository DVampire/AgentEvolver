environment_generate_agent = dict(
    name="environment_generate_agent",
    type="EnvironmentGenerateAgent",
    description="An agent that generates a new environment Python class and config dict from a description.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="environment_generate_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_step=30,
    review_steps=5,
    require_grad=False,
    use_memory=True,
)
