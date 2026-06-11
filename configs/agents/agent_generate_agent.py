agent_generate_agent = dict(
    name="agent_generate_agent",
    type="AgentGenerateAgent",
    description="An agent that generates a new agent Python class and optional HTML prompt from a description.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="agent_generate_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_step=30,
    review_steps=5,
    require_grad=False,
    use_memory=True,
)
