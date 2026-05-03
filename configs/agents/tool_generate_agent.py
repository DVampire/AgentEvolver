tool_generate_agent = dict(
    name="tool_generate_agent",
    type="ToolGenerateAgent",
    description="An agent that generates new tool source code from a description.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="tool_generate_agent",
    memory_name=None,
    max_actions=10,
    max_steps=30,
    review_steps=5,
    require_grad=False,
    use_memory=False,
)
