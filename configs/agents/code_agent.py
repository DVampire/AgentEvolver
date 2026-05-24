code_agent = dict(
    base_dir="work_dir/code_agent",
    name="code_agent",
    type="Agent",
    description="A code agent that reads, writes, and edits source code files, runs tests, and commits changes using git.",
    model_name="openai/o3",
    prompt_name="code_agent",
    memory_name="file_system_memory",
    max_actions=10,
    max_steps=30,
    review_steps=5,
    require_grad=False,
    use_memory=False,
)
