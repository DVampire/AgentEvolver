deep_analyzer_v3_agent = dict(
    workdir = "workdir/deep_analyzer_v3",
    name = "deep_analyzer_v3_agent",
    type = "Agent",
    description = (
        "Multi-round analysis agent supporting pure-text tasks and multimodal "
        "text+image/pdf/audio/video tasks (task as text, files as local image paths or image URLs). "
        "Images, pdf and audio accept any URL or local path; "
        "video supports YouTube URLs only. Runs multiple LLMs in parallel per round "
        "and synthesizes findings across up to 3 rounds."
    ),
    model_name = "openrouter/gemini-3.1-pro-preview",
    require_grad = False,
    use_memory = False,
    max_rounds = 3,
    max_steps = 20,
    general_analyze_models = [
        "openrouter/gemini-3.1-pro-preview-plugins",
    ],
    llm_analyze_models = [
        "openrouter/gpt-5.4",
    ],
    advanced_analyze_models = [
        "openrouter/gpt-5.4-pro",
    ],
)
