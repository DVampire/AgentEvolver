skill_evaluate_agent = dict(
    name="skill_evaluate_agent",
    type="SkillEvaluateAgent",
    description="An agent that evaluates skill quality given an evaluation task.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="skill_evaluate_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_steps=20,
    review_steps=5,
    require_grad=False,
    permission_mode="read_only",
    use_memory=True,
)
