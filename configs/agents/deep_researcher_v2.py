deep_researcher_v2_agent = dict(
    workdir = "workdir/deep_researcher_v2",
    name = "deep_researcher_v2_agent",
    type = "Agent",
    description = (
        "Multi-round web research agent supporting both pure-text tasks and multimodal "
        "image+text tasks (task as text, files as local image paths or image URLs). "
        "Generates optimized search queries, retrieves and synthesizes web content, "
        "and evaluates completeness across multiple rounds."
    ),
    model_name = "openrouter/gemini-3.1-pro-preview",
    memory_name = "general_memory_system",
    require_grad = False,
    # Research config
    max_rounds = 3,
    num_results = 10,
    # Summary model for Serper results
    summary_model_name = "openrouter/gemini-3-flash-preview",
    # LLM search models for parallel search
    llm_search_models=[
        "openrouter/gemini-3.1-pro-preview-plugins",
    ],
    # Page fetch config
    fetch_timeout = 20.0,
    
)
