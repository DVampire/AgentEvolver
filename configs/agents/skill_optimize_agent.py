skill_optimize_agent = dict(
    name="skill_optimize_agent",
    type="SkillOptimizeAgent",
    description="An agent that evolves a skill's SKILL.md given an optimization task.",
    model_name="openrouter/gemini-3-flash-preview",
    prompt_name="skill_optimize_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_step=30,
    review_steps=5,
    require_grad=False,
    use_memory=False,
)
