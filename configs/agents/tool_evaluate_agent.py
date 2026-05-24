tool_evaluate_agent = dict(
    name="tool_evaluate_agent",
    type="ToolEvaluateAgent",
    description="An agent that evaluates tool behavior given an evaluation task.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="tool_evaluate_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_steps=20,
    review_steps=5,
    require_grad=False,
    permission_mode="read_only",
    use_memory=True,
)
