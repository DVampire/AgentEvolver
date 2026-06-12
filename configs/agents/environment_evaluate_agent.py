environment_evaluate_agent = dict(
    name="environment_evaluate_agent",
    type="EnvironmentEvaluateAgent",
    description="An agent that evaluates a generated environment across multiple quality dimensions.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="environment_evaluate_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_step=20,
    review_steps=5,
    require_grad=False,
    use_memory=True,
)
