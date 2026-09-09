"""Instance configuration for GameBuilderAgent."""

game_builder_agent = dict(
    name="game_builder_agent",
    type="Agent",
    model_name="llm_hub/claude-fable-5-1",
    prompt_name="game_builder_agent",
    memory_name="file_system_memory",
    include_agents=False,
    use_plan=True,
    enable_evolving=True,
    use_memory=True,
    max_step=600,
    max_token=100_000_000,
    timeout=28800,
    compact_after_steps=0,
    compact_body_tokens=0,
    compact_input_tokens=50000,
)
