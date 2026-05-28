agent_optimize_agent = dict(
    name="agent_optimize_agent",
    type="AgentOptimizeAgent",
    description="An agent that evolves a generated agent (Python class and/or HTML prompt) given an optimization task.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="agent_optimize_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_steps=30,
    review_steps=5,
    require_grad=False,
    use_memory=False,
)
