wiki_searcher_agent = dict(
    workdir = "workdir/wiki_searcher",
    name = "wiki_searcher_agent",
    type = "Agent",
    description = "A Wikipedia research agent that uses wiki_search_skill to find and synthesize encyclopedic information.",
    model_name = "openrouter/gemini-3-flash-preview",
    prompt_name = "wiki_searcher_agent",
    memory_name = "general_memory_system",
    max_tools = 5,
    max_steps = 20,
    review_steps = 5,
    require_grad = False,
    use_memory = False,
)
