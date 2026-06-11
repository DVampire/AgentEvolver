meta_agent = dict(
    base_dir = "work_dir/meta_agent",
    name = "meta_agent",
    type = "Agent",
    description = "Orchestrator that decomposes tasks, dispatches sub-agents concurrently, reacts to results, and triggers self-evolution when agents underperform.",
    model_name = "openai/o3",
    prompt_name = "meta_agent",
    memory_name = "file_system_memory",
    max_step = 50,
    evolution_score_threshold = 0.5,
    require_grad = False,
    use_memory = True,
)
