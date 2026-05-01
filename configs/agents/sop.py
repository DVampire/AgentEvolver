sop_agent = dict(
    workdir = "workdir/sop",
    name = "sop_agent",
    type = "Agent",
    description = (
        "A subagent that loads domain-specific SOP (Standard Operating "
        "Procedure) skills and executes them phase-by-phase via tool calls."
    ),
    model_name = "openai/o3",
    prompt_name = "sop_agent",
    memory_name = "general_memory_system",
    max_tools = 10,
    max_steps = 50,
    review_steps = 5,
    log_max_length = 1000,
    require_grad = False,
    use_memory = True,
)
