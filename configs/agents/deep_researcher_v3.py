deep_researcher_v3_agent = dict(
    workdir = "workdir/deep_researcher_v3",
    name = "deep_researcher_v3_agent",
    type = "Agent",
    description = (
        "ThinkOutput-driven multi-round web research agent supporting pure-text and multimodal "
        "image+text tasks. Five internal tools: plan / query / search / eval / finish. "
        "The LLM selects the next tool each step; search runs concurrent API + LLM web searches "
        "and synthesizes labeled reports; eval detects conflicts and judges completeness."
    ),
    model_name = "openrouter/gemini-3.1-pro-preview",
    require_grad = False,
    use_memory = False,
    max_rounds = 3,
    max_steps = 20,
    # Research config
    num_results = 10,
    # Summary model for page summarization and synthesis
    summary_model_name = "openrouter/gemini-3-flash-preview",
    # LLM search models for parallel web search
    llm_search_models = [
        "openrouter/gemini-3.1-pro-preview-plugins",
    ],
    # Page fetch timeout (seconds)
    fetch_timeout = 20.0,
    # Whether to save per-round search results to disk
    enable_search_log = True,
)
