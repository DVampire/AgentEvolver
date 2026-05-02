tool_optimizer = dict(
    name="tool_optimizer",
    type="ToolOptimizer",
    description="An optimizer agent that evolves tool source code given an evolution task.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="tool_optimizer",
    memory_name=None,
    max_actions=10,
    max_steps=30,
    review_steps=5,
    require_grad=False,
    use_memory=False,
)
